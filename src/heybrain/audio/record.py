"""Microphone capture that stops on Enter.

macOS-first adapter (sounddevice/PortAudio) behind a platform-neutral
signature — callers just get a `Path` to a finished WAV file and don't
need to know how the recording happened.
"""

from __future__ import annotations

import threading
import wave
from pathlib import Path
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


def record_until_enter() -> Path:
    """Record from the default microphone until the user presses Enter.

    Returns the path to a 16kHz mono WAV file under $HEYBRAIN_HOME/tmp/.
    """
    settings = get_settings()
    out_path = settings.tmp_dir / f"{uuid4().hex}.wav"

    frames: list[np.ndarray] = []
    stop_event = threading.Event()

    def _callback(indata: np.ndarray, frame_count: int, time_info: object, status: object) -> None:
        frames.append(indata.copy())

    def _wait_for_enter() -> None:
        input()
        stop_event.set()

    print("\U0001f399 Listening… [Enter to stop]")

    try:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=_callback,
        )
        with stream:
            listener = threading.Thread(target=_wait_for_enter, daemon=True)
            listener.start()
            stop_event.wait()
    except sd.PortAudioError as exc:
        raise TranscriptionError(_MIC_PERMISSION_MESSAGE) from exc

    audio = np.concatenate(frames, axis=0) if frames else np.empty((0, CHANNELS), dtype=DTYPE)

    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(audio.tobytes())

    return out_path
