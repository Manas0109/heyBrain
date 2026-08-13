"""Unit tests for AppService.recall (issue #11, plan.md §8.4).

Bedrock is faked (embed + structured); VectorStore is the real Chroma
wrapper pointed at a tmp_path directory, same pattern as
tests/test_retriever.py -- no test here talks to AWS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heybrain.bedrock.schemas import RecallSynthesis
from heybrain.core.config import Settings
from heybrain.core.models import Conversation, Memory, MemoryType
from heybrain.core.service import AppService
from heybrain.memory.vectors import VectorStore, memory_metadata
from heybrain.storage.db import get_connection
from heybrain.storage.repositories import ConversationRepo, MemoryRepo

VEC = [1.0, 0.0, 0.0, 0.0]


class FakeBedrock:
    def __init__(self, synthesis: RecallSynthesis | None = None) -> None:
        self._synthesis = synthesis
        self.structured_calls: list[tuple[list[dict], str, type]] = []
        self.embed_calls: list[list[str]] = []

    def structured(self, messages, system, schema, effort, model=None):
        self.structured_calls.append((messages, system, schema))
        assert schema is RecallSynthesis
        return self._synthesis

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
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


def _seed_memory(vector_store: VectorStore, memory_repo: MemoryRepo, **overrides) -> Memory:
    defaults = dict(
        conversation_id="conv-1",
        memory_type=MemoryType.IDEA,
        content="AI coding agents could handle whole PRs autonomously.",
        topic="ai-agents",
        importance=0.8,
    )
    defaults.update(overrides)
    memory = Memory(**defaults)
    vector_store.upsert(memory.id, VEC, memory_metadata(memory))
    return memory_repo.create(memory)


def test_recall_synthesizes_answer_with_attributed_sources(
    settings, conn, vector_store
) -> None:
    ConversationRepo(conn).create(Conversation(id="conv-1"))
    memory_repo = MemoryRepo(conn)
    memory = _seed_memory(vector_store, memory_repo)

    synthesis = RecallSynthesis(
        answer="You've been excited about AI coding agents handling full PRs.",
        source_memory_ids=[memory.id],
    )
    bedrock = FakeBedrock(synthesis)

    service = AppService(
        conn=conn,
        settings=settings,
        vector_store=vector_store,
        bedrock=bedrock,
        input_fn=lambda _p: "",
        output_fn=lambda _l: None,
    )

    result = service.recall("what were my ideas about AI coding agents?")

    assert result.answer == synthesis.answer
    assert len(result.memories) == 1
    assert result.memories[0].id == memory.id
    assert result.memories[0].memory_type == MemoryType.IDEA
    assert result.memories[0].topic == "ai-agents"
    assert result.memories[0].created_at == memory.created_at
    assert len(bedrock.structured_calls) == 1


def test_recall_with_no_memories_skips_synthesis(settings, conn, vector_store) -> None:
    ConversationRepo(conn).create(Conversation(id="conv-1"))
    bedrock = FakeBedrock()

    service = AppService(
        conn=conn,
        settings=settings,
        vector_store=vector_store,
        bedrock=bedrock,
        input_fn=lambda _p: "",
        output_fn=lambda _l: None,
    )

    result = service.recall("what did I say about quantum computing?")

    assert result.answer == "I don't have anything on that yet."
    assert result.memories == []
    assert bedrock.structured_calls == []
