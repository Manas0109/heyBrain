"""Unit tests for memory.retriever.MemoryRetriever — the read path (issue #10).

Bedrock is faked (embed only, no chat/structured needed) and VectorStore is
the real Chroma wrapper pointed at a tmp_path directory, same pattern as
tests/test_memory_service.py -- no test here talks to AWS. Memories are
hand-seeded with known importance/recency/similarity so the rerank formula
can be checked deterministically rather than by vibes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from heybrain.core.models import Conversation, Memory, MemoryStatus, MemoryType
from heybrain.memory.retriever import (
    RECENCY_HALF_LIFE_HOURS,
    MemoryRetriever,
    recency_decay,
)
from heybrain.memory.vectors import VectorStore, memory_metadata
from heybrain.storage.db import get_connection
from heybrain.storage.repositories import ConversationRepo, MemoryRepo

QUERY_VEC = [1.0, 0.0, 0.0, 0.0]
SAME_AS_QUERY = [1.0, 0.0, 0.0, 0.0]
ORTHOGONAL = [0.0, 1.0, 0.0, 0.0]


class FakeBedrock:
    """Maps query text to a canned embedding; records calls made."""

    def __init__(self) -> None:
        self.embeddings: dict[str, list[float]] = {}
        self.embed_calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [self.embeddings[t] for t in texts]


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "brain.db")
    yield connection
    connection.close()


@pytest.fixture
def memory_repo(conn) -> MemoryRepo:
    # memories.conversation_id has a FK to conversations; seed one row that
    # every hand-built Memory in this module can point at.
    ConversationRepo(conn).create(Conversation(id="conv-1"))
    return MemoryRepo(conn)


@pytest.fixture
def vector_store(tmp_path: Path) -> VectorStore:
    return VectorStore(tmp_path / "chroma")


def _make_memory(**overrides) -> Memory:
    defaults = dict(
        conversation_id="conv-1",
        memory_type=MemoryType.FACT,
        content="a memory",
        topic="kafka",
        importance=0.8,
    )
    defaults.update(overrides)
    return Memory(**defaults)


def _seed(
    vector_store: VectorStore, memory_repo: MemoryRepo, memory: Memory, embedding: list[float]
) -> Memory:
    vector_store.upsert(memory.id, embedding, memory_metadata(memory))
    return memory_repo.create(memory)


def _hours_ago(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def test_retrieve_returns_empty_list_for_empty_store(
    vector_store: VectorStore, memory_repo: MemoryRepo
) -> None:
    bedrock = FakeBedrock()
    bedrock.embeddings["anything"] = QUERY_VEC
    retriever = MemoryRetriever(
        bedrock=bedrock, vector_store=vector_store, memory_repo=memory_repo
    )

    assert retriever.retrieve("anything") == []


def test_retrieve_ranks_by_recency_when_similarity_and_importance_tie(
    vector_store: VectorStore, memory_repo: MemoryRepo
) -> None:
    fresh = _seed(
        vector_store,
        memory_repo,
        _make_memory(content="fresh", importance=0.8, created_at=_hours_ago(0)),
        SAME_AS_QUERY,
    )
    half_life_old = _seed(
        vector_store,
        memory_repo,
        _make_memory(
            content="half-life old",
            importance=0.8,
            created_at=_hours_ago(RECENCY_HALF_LIFE_HOURS),
        ),
        SAME_AS_QUERY,
    )
    ancient = _seed(
        vector_store,
        memory_repo,
        _make_memory(
            content="ancient",
            importance=0.8,
            created_at=_hours_ago(RECENCY_HALF_LIFE_HOURS * 5),
        ),
        SAME_AS_QUERY,
    )

    bedrock = FakeBedrock()
    bedrock.embeddings["query"] = QUERY_VEC
    retriever = MemoryRetriever(
        bedrock=bedrock, vector_store=vector_store, memory_repo=memory_repo
    )

    results = retriever.retrieve("query", k=3)

    assert [m.id for m in results] == [fresh.id, half_life_old.id, ancient.id]


def test_retrieve_ranks_by_importance_when_similarity_and_recency_tie(
    vector_store: VectorStore, memory_repo: MemoryRepo
) -> None:
    now = _hours_ago(0)
    high = _seed(
        vector_store,
        memory_repo,
        _make_memory(content="high importance", importance=0.9, created_at=now),
        SAME_AS_QUERY,
    )
    low = _seed(
        vector_store,
        memory_repo,
        _make_memory(content="low importance", importance=0.3, created_at=now),
        SAME_AS_QUERY,
    )

    bedrock = FakeBedrock()
    bedrock.embeddings["query"] = QUERY_VEC
    retriever = MemoryRetriever(
        bedrock=bedrock, vector_store=vector_store, memory_repo=memory_repo
    )

    results = retriever.retrieve("query", k=2)

    assert [m.id for m in results] == [high.id, low.id]


def test_retrieve_ranks_by_similarity(
    vector_store: VectorStore, memory_repo: MemoryRepo
) -> None:
    now = _hours_ago(0)
    close = _seed(
        vector_store,
        memory_repo,
        _make_memory(content="close match", importance=0.8, created_at=now),
        SAME_AS_QUERY,
    )
    far = _seed(
        vector_store,
        memory_repo,
        _make_memory(content="far match", importance=0.8, created_at=now),
        ORTHOGONAL,
    )

    bedrock = FakeBedrock()
    bedrock.embeddings["query"] = QUERY_VEC
    retriever = MemoryRetriever(
        bedrock=bedrock, vector_store=vector_store, memory_repo=memory_repo
    )

    results = retriever.retrieve("query", k=2)

    assert [m.id for m in results] == [close.id, far.id]


def test_retrieve_ignores_archived_and_superseded_memories(
    vector_store: VectorStore, memory_repo: MemoryRepo
) -> None:
    now = _hours_ago(0)
    active = _seed(
        vector_store,
        memory_repo,
        _make_memory(
            content="active", importance=0.8, created_at=now, status=MemoryStatus.ACTIVE
        ),
        SAME_AS_QUERY,
    )
    _seed(
        vector_store,
        memory_repo,
        _make_memory(
            content="archived", importance=0.9, created_at=now, status=MemoryStatus.ARCHIVED
        ),
        SAME_AS_QUERY,
    )

    bedrock = FakeBedrock()
    bedrock.embeddings["query"] = QUERY_VEC
    retriever = MemoryRetriever(
        bedrock=bedrock, vector_store=vector_store, memory_repo=memory_repo
    )

    results = retriever.retrieve("query")

    assert [m.id for m in results] == [active.id]


def test_retrieve_takes_top_k_after_overfetching(
    vector_store: VectorStore, memory_repo: MemoryRepo
) -> None:
    now = _hours_ago(0)
    for i in range(8):
        _seed(
            vector_store,
            memory_repo,
            _make_memory(content=f"memory {i}", importance=0.5 + i * 0.05, created_at=now),
            SAME_AS_QUERY,
        )

    bedrock = FakeBedrock()
    bedrock.embeddings["query"] = QUERY_VEC
    retriever = MemoryRetriever(
        bedrock=bedrock, vector_store=vector_store, memory_repo=memory_repo
    )

    results = retriever.retrieve("query", k=5)

    assert len(results) == 5
    # Highest-importance memories (0-indexed 7 down to 3) should win the tie.
    assert [m.content for m in results] == [
        "memory 7", "memory 6", "memory 5", "memory 4", "memory 3",
    ]


def test_retrieve_does_single_chroma_and_single_sqlite_call(
    vector_store: VectorStore, memory_repo: MemoryRepo
) -> None:
    now = _hours_ago(0)
    for i in range(3):
        _seed(
            vector_store,
            memory_repo,
            _make_memory(content=f"memory {i}", created_at=now),
            SAME_AS_QUERY,
        )

    search_calls = []
    get_many_calls = []
    real_search = vector_store.search
    real_get_many = memory_repo.get_many

    def counting_search(*args, **kwargs):
        search_calls.append((args, kwargs))
        return real_search(*args, **kwargs)

    def counting_get_many(*args, **kwargs):
        get_many_calls.append((args, kwargs))
        return real_get_many(*args, **kwargs)

    vector_store.search = counting_search
    memory_repo.get_many = counting_get_many

    bedrock = FakeBedrock()
    bedrock.embeddings["query"] = QUERY_VEC
    retriever = MemoryRetriever(
        bedrock=bedrock, vector_store=vector_store, memory_repo=memory_repo
    )

    retriever.retrieve("query")

    assert len(search_calls) == 1
    assert len(get_many_calls) == 1
    assert len(bedrock.embed_calls) == 1


def test_retrieve_by_topic_returns_only_active_most_recent_first(
    memory_repo: MemoryRepo, vector_store: VectorStore
) -> None:
    older = memory_repo.create(
        _make_memory(
            content="older kafka memory",
            topic="kafka",
            created_at=_hours_ago(10),
        )
    )
    newer = memory_repo.create(
        _make_memory(
            content="newer kafka memory",
            topic="kafka",
            created_at=_hours_ago(1),
        )
    )
    memory_repo.create(
        _make_memory(
            content="superseded kafka memory",
            topic="kafka",
            status=MemoryStatus.SUPERSEDED,
        )
    )
    memory_repo.create(_make_memory(content="unrelated", topic="travel"))

    bedrock = FakeBedrock()
    retriever = MemoryRetriever(
        bedrock=bedrock, vector_store=vector_store, memory_repo=memory_repo
    )

    results = retriever.retrieve_by_topic("kafka")

    assert [m.id for m in results] == [newer.id, older.id]
    assert bedrock.embed_calls == []  # topic lookup never touches Bedrock


def test_recency_decay_half_life() -> None:
    now = datetime.now(timezone.utc)
    assert recency_decay(now, now=now) == pytest.approx(1.0)
    assert recency_decay(
        now - timedelta(hours=RECENCY_HALF_LIFE_HOURS), now=now
    ) == pytest.approx(0.5)
    assert recency_decay(
        now - timedelta(hours=RECENCY_HALF_LIFE_HOURS * 2), now=now
    ) == pytest.approx(0.25)
