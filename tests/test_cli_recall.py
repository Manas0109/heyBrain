"""End-to-end CLI test for `brain recall` (issue #11).

Bedrock is faked throughout; no test here talks to AWS.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from heybrain.bedrock.schemas import RecallSynthesis
from heybrain.cli.main import app
from heybrain.core.config import Settings
from heybrain.core.models import Conversation, Memory, MemoryType
from heybrain.core.service import AppService
from heybrain.memory.vectors import VectorStore, memory_metadata
from heybrain.storage.db import get_connection
from heybrain.storage.repositories import ConversationRepo, MemoryRepo

VEC = [1.0, 0.0, 0.0, 0.0]


class FakeBedrock:
    def __init__(self, synthesis: RecallSynthesis) -> None:
        self._synthesis = synthesis

    def structured(self, messages, system, schema, effort, model=None):
        return self._synthesis

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [VEC for _ in texts]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(heybrain_home=tmp_path)


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "brain.db")
    yield connection
    connection.close()


@pytest.fixture
def vector_store(tmp_path: Path) -> VectorStore:
    return VectorStore(tmp_path / "chroma")


def test_cli_recall_command_renders_answer_and_sources(
    settings, conn, vector_store, monkeypatch
) -> None:
    ConversationRepo(conn).create(Conversation(id="conv-1"))
    memory_repo = MemoryRepo(conn)
    memory = Memory(
        conversation_id="conv-1",
        memory_type=MemoryType.IDEA,
        content="AI coding agents could handle whole PRs autonomously.",
        topic="ai-agents",
        importance=0.8,
    )
    vector_store.upsert(memory.id, VEC, memory_metadata(memory))
    memory_repo.create(memory)

    synthesis = RecallSynthesis(
        answer="You've been excited about AI coding agents handling full PRs.",
        source_memory_ids=[memory.id],
    )
    bedrock = FakeBedrock(synthesis)

    monkeypatch.setattr(
        "heybrain.cli.recall.AppService",
        lambda: AppService(
            conn=conn, settings=settings, vector_store=vector_store, bedrock=bedrock
        ),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["recall", "what were my ideas about AI coding agents?"])

    assert result.exit_code == 0
    assert synthesis.answer in result.output
    assert "idea" in result.output
    assert "ai-agents" in result.output


def test_cli_recall_command_empty_store_is_honest(
    settings, conn, vector_store, monkeypatch
) -> None:
    ConversationRepo(conn).create(Conversation(id="conv-1"))
    bedrock = FakeBedrock(RecallSynthesis(answer="unused", source_memory_ids=[]))

    monkeypatch.setattr(
        "heybrain.cli.recall.AppService",
        lambda: AppService(
            conn=conn, settings=settings, vector_store=vector_store, bedrock=bedrock
        ),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["recall", "what about quantum computing?"])

    assert result.exit_code == 0
    assert "I don't have anything on that yet." in result.output
