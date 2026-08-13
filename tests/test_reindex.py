"""Tests for `brain reindex` (issue #8): rebuild Chroma from SQLite.

Embeddings are synthetic here -- a fake embeddings model is injected into
BedrockService, so nothing in this file needs live AWS credentials.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heybrain.bedrock.client import BedrockService
from heybrain.cli.main import app
from heybrain.core.config import Settings
from heybrain.core.models import Conversation, Memory, MemoryType
from heybrain.core.service import AppService
from heybrain.memory.vectors import VectorStore
from heybrain.storage.db import get_connection
from heybrain.storage.repositories import ConversationRepo, MemoryRepo, UsageRepo

DIM = 8


class FakeEmbeddingsModel:
    """Deterministic stand-in for Bedrock's Titan embeddings model."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            rng = random.Random(text)
            vectors.append([rng.uniform(-1, 1) for _ in range(DIM)])
        return vectors


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(heybrain_home=tmp_path)


@pytest.fixture
def conn(settings: Settings):
    connection = get_connection(settings.db_path)
    yield connection
    connection.close()


def _seed_memories(conn, n: int = 5) -> list[Memory]:
    conversation_repo = ConversationRepo(conn)
    conversation = Conversation(title="t", summary="s", topic="topic")
    conversation_repo.create(conversation)

    memory_repo = MemoryRepo(conn)
    memories = [
        Memory(
            conversation_id=conversation.id,
            memory_type=MemoryType.FACT,
            content=f"fact number {i}",
            topic="kafka",
            importance=0.7,
        )
        for i in range(n)
    ]
    for memory in memories:
        memory_repo.create(memory)
    return memories


def _build_service(settings: Settings, conn) -> AppService:
    bedrock = BedrockService(
        UsageRepo(conn),
        settings,
        embeddings_model_factory=FakeEmbeddingsModel,
    )
    vector_store = VectorStore(settings.chroma_dir)
    return AppService(settings=settings, conn=conn, vector_store=vector_store, bedrock=bedrock)


def test_reindex_rebuilds_chroma_from_sqlite(settings: Settings, conn) -> None:
    memories = _seed_memories(conn)
    service = _build_service(settings, conn)

    count = service.reindex()

    assert count == len(memories)
    embedding = FakeEmbeddingsModel().embed_documents([memories[0].content])[0]
    results = service._vector_store.search(embedding, k=len(memories))
    assert memories[0].id in {memory_id for memory_id, _ in results}


def test_reindex_after_deleting_chroma_dir_is_equivalent(
    settings: Settings, conn
) -> None:
    memories = _seed_memories(conn)
    service = _build_service(settings, conn)
    service.reindex()

    query_embedding = FakeEmbeddingsModel().embed_documents([memories[2].content])[0]
    before = service._vector_store.search(query_embedding, k=len(memories))

    import shutil

    service._vector_store.close()
    shutil.rmtree(settings.chroma_dir)

    rebuilt_service = _build_service(settings, conn)
    rebuilt_service.reindex()
    after = rebuilt_service._vector_store.search(query_embedding, k=len(memories))

    assert before == after


def test_cli_reindex_command(settings: Settings, conn, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_memories(conn, n=3)
    conn.close()

    monkeypatch.setattr(
        "heybrain.cli.main.AppService",
        lambda: _build_service(settings, get_connection(settings.db_path)),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "3" in result.output
