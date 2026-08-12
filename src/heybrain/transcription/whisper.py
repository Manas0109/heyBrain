"""Local speech-to-text via faster-whisper.

Model weights are cached under $HEYBRAIN_HOME/models/ so the first call
pays a one-time download; `warm_model()` lets `brain doctor` (#6) pay
that cost up front instead of on the user's first `brain think --voice`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from faster_whisper import WhisperModel

from heybrain.core.config import get_settings
from heybrain.core.errors import TranscriptionError

_EMPTY_TRANSCRIPT_MESSAGE = (
    "Didn't catch any speech in that recording. Try again and speak "
    "clearly after the listening indicator appears."
)


@lru_cache
def _get_model() -> WhisperModel:
    settings = get_settings()
    return WhisperModel(
        settings.whisper_model,
        download_root=str(settings.models_dir),
        compute_type="int8",
    )


def warm_model() -> None:
    """Load (and if needed, download) the whisper model ahead of time."""
    _get_model()


def transcribe(path: Path) -> str:
    """Transcribe a WAV file at `path`, deleting it unconditionally afterward."""
    try:
        model = _get_model()
        segments, _info = model.transcribe(str(path))
        text = "".join(segment.text for segment in segments).strip()
    finally:
        path.unlink(missing_ok=True)

    if not text:
        raise TranscriptionError(_EMPTY_TRANSCRIPT_MESSAGE)

    return text
