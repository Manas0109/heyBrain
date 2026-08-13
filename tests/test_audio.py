from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import sounddevice as sd

from heybrain.audio import record
from heybrain.core.config import Settings
from heybrain.core.errors import TranscriptionError


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    made = Settings(heybrain_home=tmp_path / "home")
    made.ensure_home()
    monkeypatch.setattr(record, "get_settings", lambda: made)
    return made


class _FakeStream:
    """Stands in for sd.InputStream: delivers one chunk of audio on entry."""

    def __init__(self, *args, callback=None, **kwargs) -> None:
        self._callback = callback

    def __enter__(self) -> "_FakeStream":
        self._callback(np.zeros((160, 1), dtype="int16"), 160, None, None)
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _RaisingStream:
    def __init__(self, *args, **kwargs) -> None:
        raise sd.PortAudioError("Permission denied")


def test_record_until_enter_writes_16khz_mono_wav(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(record, "sd", SimpleNamespace(InputStream=_FakeStream, PortAudioError=sd.PortAudioError))
    monkeypatch.setattr("builtins.input", lambda: "")

    out_path = record.record_until_enter()

    assert out_path.parent == settings.tmp_dir
    with wave.open(str(out_path), "rb") as wav_file:
        assert wav_file.getframerate() == 16_000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2


def test_record_until_enter_mic_permission_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        record, "sd", SimpleNamespace(InputStream=_RaisingStream, PortAudioError=sd.PortAudioError)
    )
    monkeypatch.setattr("builtins.input", lambda: "")

    with pytest.raises(TranscriptionError, match="System Settings"):
        record.record_until_enter()
