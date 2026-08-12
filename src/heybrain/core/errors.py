"""Error hierarchy for heyBrain.

All exceptions that can cross a module boundary and surface to the CLI
derive from HeyBrainError. The CLI catches this base class and prints a
human sentence, never a stack trace.
"""

from __future__ import annotations


class HeyBrainError(Exception):
    """Base class for all heyBrain errors."""


class BedrockError(HeyBrainError):
    """Raised when a Bedrock request fails or returns an unusable response."""

    def __init__(self, message: str, *, recoverable: bool = False) -> None:
        super().__init__(message)
        self.recoverable = recoverable


class TranscriptionError(HeyBrainError):
    """Raised when audio capture or speech-to-text fails."""


class StorageError(HeyBrainError):
    """Raised when SQLite or Chroma storage operations fail."""
