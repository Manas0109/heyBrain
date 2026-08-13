"""Application configuration.

Settings are read from the environment / .env via pydantic-settings.
Accessing `get_settings()` also ensures HEYBRAIN_HOME exists on disk.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_region: str = "us-east-1"
    aws_profile: str | None = None

    bedrock_model_id: str = "anthropic.claude-opus-5"
    bedrock_fast_model_id: str = "anthropic.claude-haiku-4-5"
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"

    heybrain_home: Path = Path.home() / ".heybrain"

    whisper_model: str = "base.en"

    @property
    def db_path(self) -> Path:
        return self.heybrain_home / "brain.db"

    @property
    def chroma_dir(self) -> Path:
        return self.heybrain_home / "chroma"

    @property
    def tmp_dir(self) -> Path:
        return self.heybrain_home / "tmp"

    @property
    def models_dir(self) -> Path:
        return self.heybrain_home / "models"

    def ensure_home(self) -> None:
        self.heybrain_home.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_home()
    return settings
