"""Microphone capture with a press-Enter-to-start / press-Enter-to-stop toggle.

macOS-first adapter (sounddevice/PortAudio) behind a platform-neutral
signature — callers just get a `Path` to a finished WAV file and don't
need to know how the recording happened.

Recording is a toggle, not hold-to-talk: the user presses Enter once to
start and once to stop. Hold-to-talk was considered and rejected (plan.md
§10) because it needs `pynput` plus macOS Accessibility permissions just to
detect a held key from the terminal -- too much extra surface for a
hackathon build.
"""

from __future__ import annotations

import threading
import wave
from pathlib import Path
from typing import Callable
from uuid import uuid4

import numpy as np
import sounddevice as sd

from heybrain.core.config import get_settings
from heybrain.core.errors import TranscriptionError

SAMPLE_RATE = 16_000
CHANNELS = 1
DTYPE = "int16"
SAMPLE_WIDTH_BYTES = 2

_MIC_PERMISSION_MESSAGE = (
    "Could not access the microphone. On macOS, grant access under "
    "System Settings → Privacy & Security → Microphone, then try again."
)

# Opening/closing sd.InputStream (PortAudio -> CoreAudio on macOS) is the
# operation known to occasionally deadlock -- with no exception and no CPU
# use -- when repeated rapidly within one process. These bounds exist purely
# so that failure mode surfaces as a clear error instead of an infinite hang.
# They're generous on purpose (device negotiation genuinely can take a
# couple of seconds on a loaded machine) but finite: any legitimate open or
# close finishes in well under this, so hitting the bound means something is
# actually stuck.
_STREAM_OPEN_TIMEOUT_SECONDS = 10.0
_STREAM_CLOSE_TIMEOUT_SECONDS = 5.0

_STREAM_OPEN_TIMEOUT_MESSAGE = (
    "Timed out opening the microphone stream (waited "
    f"{_STREAM_OPEN_TIMEOUT_SECONDS:.0f}s). This is a known class of "
    "PortAudio/CoreAudio issue on macOS; try again, and if it keeps "
    "happening restart the terminal."
)
_STREAM_CLOSE_TIMEOUT_MESSAGE = (
    "Timed out closing the microphone stream (waited "
    f"{_STREAM_CLOSE_TIMEOUT_SECONDS:.0f}s). This is a known class of "
    "PortAudio/CoreAudio issue on macOS; try again, and if it keeps "
    "happening restart the terminal."
)


def _run_with_timeout(fn: Callable[[], None], timeout_seconds: float, timeout_message: str) -> None:
    """Run `fn` on a background thread and bound how long we'll wait for it.

    Native PortAudio calls have no cooperative way to cancel them, so a truly
    stuck call leaks a daemon thread rather than dying -- but the caller
    always gets an answer (a `TranscriptionError`) instead of hanging
    forever.
    """
    errors: list[BaseException] = []

    def _target() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread below
            errors.append(exc)

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise TranscriptionError(timeout_message)
    if errors:
        raise errors[0]


class VoiceRecordSession:
    """One microphone stream, reused across every turn of a voice session.

    `brain think --voice` previously opened a fresh `sd.InputStream` per
    turn and closed it immediately after. A user reported that recording
    would hang indefinitely (no traceback, no CPU activity) on the 3rd
    consecutive voice turn -- consistent with a known class of
    PortAudio/CoreAudio deadlock triggered by rapidly closing and reopening
    an input stream within the same process on macOS.

    This session opens the stream lazily on the first turn and keeps it
    open for the life of the session; each `record_turn()` call just toggles
    whether the callback keeps incoming frames or drops them. That avoids
    the repeated open/close pattern entirely for everything but the first
    open and the final close, which is a much smaller surface for the same
    deadlock -- and both are still wrapped in `_run_with_timeout` so even
    that smaller surface can't hang silently.
    """

    def __init__(self) -> None:
        self._stream: sd.InputStream | None = None
        self._frames: list[np.ndarray] = []
        self._recording = False
        self._lock = threading.Lock()

    def _callback(self, indata: np.ndarray, frame_count: int, time_info: object, status: object) -> None:
        if self._recording:
            with self._lock:
                self._frames.append(indata.copy())

    def _ensure_stream_open(self) -> None:
        if self._stream is not None:
            return

        def _open() -> None:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                callback=self._callback,
            )
            stream.start()
            self._stream = stream

        try:
            _run_with_timeout(_open, _STREAM_OPEN_TIMEOUT_SECONDS, _STREAM_OPEN_TIMEOUT_MESSAGE)
        except sd.PortAudioError as exc:
            raise TranscriptionError(_MIC_PERMISSION_MESSAGE) from exc

    def record_turn(self) -> Path:
        """Block for Enter, record until the next Enter, return the WAV path.

        Returns the path to a 16kHz mono WAV file under $HEYBRAIN_HOME/tmp/.
        """
        settings = get_settings()
        out_path = settings.tmp_dir / f"{uuid4().hex}.wav"

        print("Press Enter to start recording…")
        input()

        # Flip the flag before the stream (which may still need to open)
        # so no frames from the very start of the take are dropped.
        with self._lock:
            self._frames = []
        self._recording = True
        self._ensure_stream_open()

        print("\U0001f399 Recording… [Enter to stop]")
        input()

        self._recording = False
        with self._lock:
            frames, self._frames = self._frames, []

        audio = np.concatenate(frames, axis=0) if frames else np.empty((0, CHANNELS), dtype=DTYPE)

        with wave.open(str(out_path), "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(audio.tobytes())

        return out_path

    def close(self) -> None:
        """Stop and close the stream. Safe to call more than once, or never having opened."""
        stream, self._stream = self._stream, None
        if stream is None:
            return

        def _close() -> None:
            stream.stop()
            stream.close()

        _run_with_timeout(_close, _STREAM_CLOSE_TIMEOUT_SECONDS, _STREAM_CLOSE_TIMEOUT_MESSAGE)


def record_until_enter() -> Path:
    """Record one ad-hoc toggle turn: open a stream, record, close it.

    For a multi-turn `brain think --voice` session, use `VoiceRecordSession`
    directly and call `record_turn()` per turn instead -- that's what keeps
    the microphone stream open across turns (see its docstring). This
    function exists for single-shot callers and is just a session used once.
    """
    session = VoiceRecordSession()
    try:
        return session.record_turn()
    finally:
        session.close()
