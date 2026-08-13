from __future__ import annotations

import os
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from heybrain.core.errors import TranscriptionError
from heybrain.transcription import whisper


def _write_wav(path: Path, seconds: float, *, sample_rate: int = 16_000) -> None:
    samples = np.zeros(int(seconds * sample_rate), dtype="int16")
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples.tobytes())


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModel:
    def __init__(self, segments: list[_FakeSegment]) -> None:
        self._segments = segments

    def transcribe(self, path: str) -> tuple[list[_FakeSegment], object]:
        return self._segments, None


class _RaisingModel:
    def transcribe(self, path: str) -> tuple[list[_FakeSegment], object]:
        raise RuntimeError("model exploded")


def test_transcribe_returns_text_and_deletes_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(whisper, "_get_model", lambda: _FakeModel([_FakeSegment(" hello world ")]))
    wav_path = tmp_path / "clip.wav"
    _write_wav(wav_path, seconds=1)

    text = whisper.transcribe(wav_path)

    assert text == "hello world"
    assert not wav_path.exists()


def test_transcribe_silence_raises_transcription_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(whisper, "_get_model", lambda: _FakeModel([_FakeSegment("   ")]))
    wav_path = tmp_path / "silence.wav"
    _write_wav(wav_path, seconds=1)

    with pytest.raises(TranscriptionError):
        whisper.transcribe(wav_path)

    assert not wav_path.exists()


def test_transcribe_deletes_temp_file_even_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(whisper, "_get_model", lambda: _RaisingModel())
    wav_path = tmp_path / "boom.wav"
    _write_wav(wav_path, seconds=1)

    with pytest.raises(RuntimeError):
        whisper.transcribe(wav_path)

    assert not wav_path.exists()


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("HEYBRAIN_RUN_SLOW_TESTS") != "1",
    reason="needs the real faster-whisper model and takes real wall-clock time; "
    "set HEYBRAIN_RUN_SLOW_TESTS=1 to run",
)
def test_transcribe_20s_clip_under_3_seconds(tmp_path: Path) -> None:
    wav_path = tmp_path / "long.wav"
    _write_wav(wav_path, seconds=20)

    start = time.monotonic()
    with pytest.raises(TranscriptionError):
        # Silence still exercises the full model load + inference path;
        # what's being timed is inference speed, not transcript content.
        whisper.transcribe(wav_path)
    elapsed = time.monotonic() - start

    assert elapsed < 3.0
