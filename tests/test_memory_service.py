"""Unit tests for memory.service.MemoryService — the write path (issue #9).

This is the hardest and highest-risk piece of the build: without correct
dedup the memory store degenerates into near-duplicates (plan.md §8.2).
Bedrock is faked and VectorStore is the real Chroma wrapper pointed at a
tmp_path directory — no test here talks to AWS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heybrain.bedrock.schemas import MemoryCandidate
from heybrain.core.errors import BedrockError
from heybrain.core.models import Conversation, MemoryStatus, MemoryType, Message, Role
from heybrain.memory.extractor import _ExtractionResult
from heybrain.memory.service import (
    DEDUPE_SIMILARITY_THRESHOLD,
    IMPORTANCE_THRESHOLD,
    DedupeVerdict,
    MemoryService,
)
from heybrain.memory.vectors import VectorStore
from heybrain.storage.db import get_connection
from heybrain.storage.repositories import ConversationRepo, MemoryRepo, MessageRepo

# Same direction -> cosine similarity 1.0, comfortably over the dedup
# threshold. Orthogonal -> similarity 0.0, comfortably under it.
VEC_KAFKA_A = [1.0, 0.0, 0.0, 0.0]
VEC_KAFKA_B = [1.0, 0.0, 0.0, 0.0]
VEC_UNRELATED = [0.0, 1.0, 0.0, 0.0]


class FakeBedrock:
    """Queues canned responses per schema type; records embed/structured calls."""

    def __init__(self) -> None:
        self._extraction_queue: list[_ExtractionResult] = []
        self._verdict_queue: list[DedupeVerdict | Exception] = []
        self.embeddings: dict[str, list[float]] = {}
        self.embed_calls: list[list[str]] = []
        self.structured_schemas: list[type] = []

    def queue_extraction(self, candidates: list[MemoryCandidate]) -> None:
        self._extraction_queue.append(_ExtractionResult(memory_candidates=candidates))

    def queue_verdict(self, verdict: DedupeVerdict | Exception) -> None:
        self._verdict_queue.append(verdict)

    def structured(self, messages, system, schema, effort, model=None):
        self.structured_schemas.append(schema)
        if schema is _ExtractionResult:
            return self._extraction_queue.pop(0)
        if schema is DedupeVerdict:
            item = self._verdict_queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        raise AssertionError(f"unexpected schema: {schema}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [self.embeddings[t] for t in texts]


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "brain.db")
    yield connection
    connection.close()


@pytest.fixture
def vector_store(tmp_path: Path) -> VectorStore:
    return VectorStore(tmp_path / "chroma")


@pytest.fixture
def repos(conn):
    return ConversationRepo(conn), MessageRepo(conn), MemoryRepo(conn)


def _conversation(conversation_repo: ConversationRepo) -> Conversation:
    return conversation_repo.create(Conversation())


def _seed_message(message_repo: MessageRepo, conversation_id: str, content: str) -> None:
    message_repo.create(
        Message(conversation_id=conversation_id, role=Role.USER, content=content)
    )


def test_dedup_across_separate_sessions_merges_into_one_memory(
    vector_store, repos
) -> None:
    conversation_repo, message_repo, memory_repo = repos
    bedrock = FakeBedrock()
    service = MemoryService(
        bedrock=bedrock,
        vector_store=vector_store,
        memory_repo=memory_repo,
        message_repo=message_repo,
    )

    first_text = "User wants to learn Kafka for system design interview prep."
    second_text = "User should probably study Kafka before interviews."
    merged_text = "User wants to learn Kafka for system design interview prep."
    bedrock.embeddings = {first_text: VEC_KAFKA_A, second_text: VEC_KAFKA_B}

    conv1 = _conversation(conversation_repo)
    _seed_message(message_repo, conv1.id, "I want to learn Kafka for system design prep.")
    bedrock.queue_extraction(
        [MemoryCandidate(content=first_text, memory_type="goal", importance=0.8, topic="kafka")]
    )
    stored_first = service.process_conversation(conv1.id)
    assert len(stored_first) == 1

    conv2 = _conversation(conversation_repo)
    _seed_message(message_repo, conv2.id, "I should probably study Kafka before interviews.")
    bedrock.queue_extraction(
        [MemoryCandidate(content=second_text, memory_type="goal", importance=0.8, topic="kafka")]
    )
    bedrock.queue_verdict(DedupeVerdict(verdict="merge", merged_content=merged_text))
    stored_second = service.process_conversation(conv2.id)

    all_memories = memory_repo.list_all()
    assert len(all_memories) == 1
    assert stored_second[0].id == all_memories[0].id
    assert all_memories[0].content == merged_text
    assert all_memories[0].status == MemoryStatus.ACTIVE


def test_dedup_supersede_marks_old_row_and_inserts_new(vector_store, repos) -> None:
    conversation_repo, message_repo, memory_repo = repos
    bedrock = FakeBedrock()
    service = MemoryService(
        bedrock=bedrock,
        vector_store=vector_store,
        memory_repo=memory_repo,
        message_repo=message_repo,
    )

    first_text = "User wants to learn Kafka for system design interview prep."
    second_text = "User has already started studying Kafka for interviews."
    bedrock.embeddings = {first_text: VEC_KAFKA_A, second_text: VEC_KAFKA_B}

    conv1 = _conversation(conversation_repo)
    _seed_message(message_repo, conv1.id, "I want to learn Kafka for system design prep.")
    bedrock.queue_extraction(
        [MemoryCandidate(content=first_text, memory_type="goal", importance=0.8, topic="kafka")]
    )
    [original] = service.process_conversation(conv1.id)

    conv2 = _conversation(conversation_repo)
    _seed_message(message_repo, conv2.id, "I've already started studying Kafka for interviews.")
    bedrock.queue_extraction(
        [MemoryCandidate(content=second_text, memory_type="goal", importance=0.8, topic="kafka")]
    )
    bedrock.queue_verdict(DedupeVerdict(verdict="supersede"))
    [new_memory] = service.process_conversation(conv2.id)

    all_memories = {m.id: m for m in memory_repo.list_all()}
    assert len(all_memories) == 2
    assert all_memories[original.id].status == MemoryStatus.SUPERSEDED
    assert all_memories[new_memory.id].status == MemoryStatus.ACTIVE
    assert all_memories[new_memory.id].content == second_text

    active = [m for m in all_memories.values() if m.status == MemoryStatus.ACTIVE]
    assert len(active) == 1


def test_two_different_candidates_produce_two_memories(vector_store, repos) -> None:
    conversation_repo, message_repo, memory_repo = repos
    bedrock = FakeBedrock()
    service = MemoryService(
        bedrock=bedrock,
        vector_store=vector_store,
        memory_repo=memory_repo,
        message_repo=message_repo,
    )

    kafka_text = "User wants to learn Kafka for system design interview prep."
    unrelated_text = "User is planning a trip to Japan next spring."
    bedrock.embeddings = {kafka_text: VEC_KAFKA_A, unrelated_text: VEC_UNRELATED}

    conversation = _conversation(conversation_repo)
    _seed_message(message_repo, conversation.id, "Kafka prep, and also planning a trip.")
    bedrock.queue_extraction(
        [
            MemoryCandidate(
                content=kafka_text, memory_type="goal", importance=0.8, topic="kafka"
            ),
            MemoryCandidate(
                content=unrelated_text, memory_type="plan", importance=0.7, topic="travel"
            ),
        ]
    )

    stored = service.process_conversation(conversation.id)

    assert len(stored) == 2
    assert {m.content for m in stored} == {kafka_text, unrelated_text}
    # No dedupe verdict call should have happened -- similarity was below
    # DEDUPE_SIMILARITY_THRESHOLD for the second candidate.
    assert DedupeVerdict not in bedrock.structured_schemas


def test_low_importance_chatter_produces_no_memory(vector_store, repos) -> None:
    conversation_repo, message_repo, memory_repo = repos
    bedrock = FakeBedrock()
    service = MemoryService(
        bedrock=bedrock,
        vector_store=vector_store,
        memory_repo=memory_repo,
        message_repo=message_repo,
    )
    assert IMPORTANCE_THRESHOLD == 0.6

    conversation = _conversation(conversation_repo)
    _seed_message(message_repo, conversation.id, "I'm tired today.")
    bedrock.queue_extraction(
        [
            MemoryCandidate(
                content="I'm tired today.",
                memory_type="fact",
                importance=0.2,
                topic="mood",
            )
        ]
    )

    stored = service.process_conversation(conversation.id)

    assert stored == []
    assert memory_repo.list_all() == []
    assert bedrock.embed_calls == []  # filtered before embedding, no wasted call


def test_dedup_llm_failure_falls_back_to_separate(vector_store, repos) -> None:
    conversation_repo, message_repo, memory_repo = repos
    bedrock = FakeBedrock()
    service = MemoryService(
        bedrock=bedrock,
        vector_store=vector_store,
        memory_repo=memory_repo,
        message_repo=message_repo,
    )

    first_text = "User wants to learn Kafka for system design interview prep."
    second_text = "User should probably study Kafka before interviews."
    bedrock.embeddings = {first_text: VEC_KAFKA_A, second_text: VEC_KAFKA_B}

    conv1 = _conversation(conversation_repo)
    _seed_message(message_repo, conv1.id, "I want to learn Kafka for system design prep.")
    bedrock.queue_extraction(
        [MemoryCandidate(content=first_text, memory_type="goal", importance=0.8, topic="kafka")]
    )
    [original] = service.process_conversation(conv1.id)

    conv2 = _conversation(conversation_repo)
    _seed_message(message_repo, conv2.id, "I should probably study Kafka before interviews.")
    bedrock.queue_extraction(
        [MemoryCandidate(content=second_text, memory_type="goal", importance=0.8, topic="kafka")]
    )
    bedrock.queue_verdict(BedrockError("dedupe call timed out", recoverable=True))

    # Must not raise -- a dedup LLM failure degrades to "separate" so the
    # candidate is never lost.
    [new_memory] = service.process_conversation(conv2.id)

    all_memories = {m.id: m for m in memory_repo.list_all()}
    assert len(all_memories) == 2
    assert all_memories[original.id].status == MemoryStatus.ACTIVE
    assert all_memories[original.id].content == first_text
    assert all_memories[new_memory.id].content == second_text


def test_write_candidate_similarity_below_threshold_inserts_directly(
    vector_store, repos
) -> None:
    conversation_repo, message_repo, memory_repo = repos
    bedrock = FakeBedrock()
    service = MemoryService(
        bedrock=bedrock,
        vector_store=vector_store,
        memory_repo=memory_repo,
        message_repo=message_repo,
    )
    assert DEDUPE_SIMILARITY_THRESHOLD == pytest.approx(0.90)

    candidate = MemoryCandidate(
        content="User prefers backend over frontend.",
        memory_type="preference",
        importance=0.9,
        topic="career",
    )
    bedrock.embeddings = {candidate.content: VEC_KAFKA_A}

    conversation = _conversation(conversation_repo)
    memory = service.write_candidate(candidate, conversation.id)

    assert memory.memory_type == MemoryType.PREFERENCE
    assert memory.content == candidate.content
