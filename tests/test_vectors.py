"""Unit tests for VectorStore (issue #8).

Uses synthetic random vectors throughout -- this module never talks to
Bedrock or AWS, so tests need no live credentials.
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path

import pytest

from heybrain.core.models import Memory, MemoryStatus, MemoryType
from heybrain.memory.vectors import VectorStore

DIM = 16


def _vector(seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.uniform(-1, 1) for _ in range(DIM)]


def _make_memory(**overrides) -> Memory:
    defaults = dict(
        conversation_id="conv-1",
        memory_type=MemoryType.FACT,
        content="a synthetic memory",
        topic="kafka",
        importance=0.7,
    )
    defaults.update(overrides)
    return Memory(**defaults)


def _metadata(memory: Memory) -> dict:
    return {
        "memory_type": memory.memory_type.value,
        "topic": memory.topic,
        "importance": memory.importance,
        "status": memory.status.value,
        "created_at": memory.created_at.isoformat(),
        "conversation_id": memory.conversation_id,
    }


@pytest.fixture
def chroma_dir(tmp_path: Path) -> Path:
    return tmp_path / "chroma"


@pytest.fixture
def store(chroma_dir: Path) -> VectorStore:
    return VectorStore(chroma_dir)


def test_upsert_and_search_returns_sensible_neighbours(store: VectorStore) -> None:
    memories = [_make_memory(topic="kafka") for _ in range(20)]
    for i, memory in enumerate(memories):
        store.upsert(memory.id, _vector(i), _metadata(memory))

    results = store.search(_vector(5), k=3)

    assert results[0][0] == memories[5].id
    assert results[0][1] == pytest.approx(0.0, abs=1e-6)
    assert len(results) == 3


def test_search_filters_status_active_by_default(store: VectorStore) -> None:
    active = _make_memory(status=MemoryStatus.ACTIVE)
    archived = _make_memory(status=MemoryStatus.ARCHIVED)
    store.upsert(active.id, _vector(1), _metadata(active))
    store.upsert(archived.id, _vector(1), _metadata(archived))

    results = store.search(_vector(1), k=5)

    assert [memory_id for memory_id, _ in results] == [active.id]


def test_search_filters_by_topic(store: VectorStore) -> None:
    kafka = _make_memory(topic="kafka")
    db = _make_memory(topic="databases")
    store.upsert(kafka.id, _vector(2), _metadata(kafka))
    store.upsert(db.id, _vector(2), _metadata(db))

    results = store.search(_vector(2), k=5, filters={"topic": "kafka"})

    assert [memory_id for memory_id, _ in results] == [kafka.id]


def test_search_filters_combine_status_and_topic(store: VectorStore) -> None:
    active_kafka = _make_memory(topic="kafka", status=MemoryStatus.ACTIVE)
    archived_kafka = _make_memory(topic="kafka", status=MemoryStatus.ARCHIVED)
    active_other = _make_memory(topic="other", status=MemoryStatus.ACTIVE)
    for memory in (active_kafka, archived_kafka, active_other):
        store.upsert(memory.id, _vector(3), _metadata(memory))

    results = store.search(_vector(3), k=5, filters={"topic": "kafka"})

    assert [memory_id for memory_id, _ in results] == [active_kafka.id]


def test_delete_removes_vector(store: VectorStore) -> None:
    memory = _make_memory()
    store.upsert(memory.id, _vector(4), _metadata(memory))

    store.delete(memory.id)
    results = store.search(_vector(4), k=5)

    assert results == []


def test_rebuild_replaces_collection_contents(store: VectorStore) -> None:
    stale = _make_memory()
    store.upsert(stale.id, _vector(10), _metadata(stale))

    fresh = [_make_memory() for _ in range(3)]
    embeddings = [_vector(20 + i) for i in range(3)]
    store.rebuild(fresh, embeddings)

    results = store.search(_vector(10), k=10)
    ids = {memory_id for memory_id, _ in results}
    assert stale.id not in ids
    assert ids == {memory.id for memory in fresh}


def test_search_survives_deleting_and_reindexing_chroma_dir(
    chroma_dir: Path, store: VectorStore
) -> None:
    memories = [_make_memory() for _ in range(20)]
    embeddings = [_vector(i) for i in range(20)]
    for memory, embedding in zip(memories, embeddings):
        store.upsert(memory.id, embedding, _metadata(memory))

    before = store.search(_vector(7), k=5)
    store.close()
    shutil.rmtree(chroma_dir)

    rebuilt = VectorStore(chroma_dir)
    rebuilt.rebuild(memories, embeddings)
    after = rebuilt.search(_vector(7), k=5)

    assert [memory_id for memory_id, _ in before] == [memory_id for memory_id, _ in after]
