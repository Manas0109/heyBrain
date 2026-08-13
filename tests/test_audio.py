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
    """Stands in for sd.InputStream: delivers one chunk of audio on start()."""

    instances = 0

    def __init__(self, *args, callback=None, **kwargs) -> None:
        _FakeStream.instances += 1
        self._callback = callback
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self._callback(np.zeros((160, 1), dtype="int16"), 160, None, None)

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class _RaisingStream:
    def __init__(self, *args, **kwargs) -> None:
        raise sd.PortAudioError("Permission denied")


class _HangingStream:
    """Never returns from start() -- simulates a stuck PortAudio/CoreAudio open."""

    def __init__(self, *args, callback=None, **kwargs) -> None:
        pass

    def start(self) -> None:
        import time

        time.sleep(10)


def test_record_until_enter_writes_16khz_mono_wav(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeStream.instances = 0
    monkeypatch.setattr(record, "sd", SimpleNamespace(InputStream=_FakeStream, PortAudioError=sd.PortAudioError))
    monkeypatch.setattr("builtins.input", lambda: "")

    out_path = record.record_until_enter()

    assert out_path.parent == settings.tmp_dir
    with wave.open(str(out_path), "rb") as wav_file:
        assert wav_file.getframerate() == 16_000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2


def test_record_until_enter_toggles_on_two_enter_presses(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(record, "sd", SimpleNamespace(InputStream=_FakeStream, PortAudioError=sd.PortAudioError))
    presses = iter(["", ""])
    call_count = 0

    def _input() -> str:
        nonlocal call_count
        call_count += 1
        return next(presses)

    monkeypatch.setattr("builtins.input", _input)

    record.record_until_enter()

    assert call_count == 2


def test_record_until_enter_mic_permission_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        record, "sd", SimpleNamespace(InputStream=_RaisingStream, PortAudioError=sd.PortAudioError)
    )
    monkeypatch.setattr("builtins.input", lambda: "")

    with pytest.raises(TranscriptionError, match="System Settings"):
        record.record_until_enter()


def test_record_until_enter_stream_open_timeout(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(record, "sd", SimpleNamespace(InputStream=_HangingStream, PortAudioError=sd.PortAudioError))
    monkeypatch.setattr(record, "_STREAM_OPEN_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("builtins.input", lambda: "")

    with pytest.raises(TranscriptionError, match="Timed out opening"):
        record.record_until_enter()


def test_voice_record_session_reuses_one_stream_across_turns(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeStream.instances = 0
    monkeypatch.setattr(record, "sd", SimpleNamespace(InputStream=_FakeStream, PortAudioError=sd.PortAudioError))
    monkeypatch.setattr("builtins.input", lambda: "")

    session = record.VoiceRecordSession()
    try:
        session.record_turn()
        session.record_turn()
    finally:
        session.close()

    assert _FakeStream.instances == 1


def test_voice_record_session_close_stops_and_closes_stream(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(record, "sd", SimpleNamespace(InputStream=_FakeStream, PortAudioError=sd.PortAudioError))
    monkeypatch.setattr("builtins.input", lambda: "")

    session = record.VoiceRecordSession()
    session.record_turn()
    stream = session._stream
    session.close()

    assert stream.stopped
    assert stream.closed
    assert session._stream is None
