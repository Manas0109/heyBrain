"""Unit tests for `brain resume` (issue #12): AppService.list_recent_topics,
AppService.resume, and the CLI's fuzzy topic matching.

Bedrock is faked via a stub with a `.structured()` method; no test here
talks to AWS or Chroma's embedding path (retrieve_by_topic never embeds).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from heybrain.bedrock.schemas import ConversationAnalysis, ConversationTurn, TopicReconstruction
from heybrain.cli.resume import resolve_topic
from heybrain.core.config import Settings
from heybrain.core.errors import HeyBrainError
from heybrain.core.models import Conversation, ConversationStatus, Memory, MemoryType
from heybrain.core.service import AppService
from heybrain.storage.db import get_connection
from heybrain.storage.repositories import ConversationRepo, MemoryRepo, TaskRepo


class FakeBedrock:
    def __init__(
        self,
        *,
        reconstruction: TopicReconstruction | None = None,
        turns: list[ConversationTurn] | None = None,
        analysis: ConversationAnalysis | None = None,
    ) -> None:
        self._reconstruction = reconstruction
        self._turns = list(turns or [])
        self._analysis = analysis or ConversationAnalysis(
            title="t", summary="s", topic="topic", memory_candidates=[], tasks=[]
        )
        self.structured_calls: list[tuple[list[dict], str, type]] = []

    def structured(self, messages, system, schema, effort, model=None):
        self.structured_calls.append((messages, system, schema))
        if schema is TopicReconstruction:
            return self._reconstruction
        if schema is ConversationAnalysis:
            return self._analysis
        return self._turns.pop(0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 4 for _ in texts]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(heybrain_home=tmp_path)


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "brain.db")
    yield connection
    connection.close()


def _reconstruction(topic: str) -> TopicReconstruction:
    return TopicReconstruction(
        topic=topic,
        summary="You were studying Kafka fundamentals for interview prep.",
        open_threads=["What's next: partitions and consumer groups?"],
    )


# --- list_recent_topics -----------------------------------------------------


def test_list_recent_topics_ordering_and_last_touched(settings, conn) -> None:
    conversations = ConversationRepo(conn)
    memories = MemoryRepo(conn)
    now = datetime.now(timezone.utc)

    kafka_conv = conversations.create(
        Conversation(
            topic="kafka-prep",
            status=ConversationStatus.CLOSED,
            created_at=now - timedelta(days=3),
            updated_at=now - timedelta(days=3),
        )
    )
    conversations.create(
        Conversation(
            topic="vacation planning",
            status=ConversationStatus.CLOSED,
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
    )
    # A memory on kafka-prep touched more recently than its own conversation
    # row -- the merged last-touched time should pick this up and rank
    # kafka-prep above vacation planning despite the older conversation.
    memories.create(
        Memory(
            conversation_id=kafka_conv.id,
            memory_type=MemoryType.FACT,
            content="User wants to learn Kafka for system design interview prep.",
            topic="kafka-prep",
            importance=0.8,
            created_at=now,
            updated_at=now,
        )
    )

    service = AppService(conn=conn, settings=settings, bedrock=FakeBedrock())

    topics = service.list_recent_topics()

    assert [t.topic for t in topics] == ["kafka-prep", "vacation planning"]
    assert topics[0].last_touched_at == now


def test_list_recent_topics_empty_store(settings, conn) -> None:
    service = AppService(conn=conn, settings=settings, bedrock=FakeBedrock())
    assert service.list_recent_topics() == []


# --- resume ------------------------------------------------------------------


def test_resume_opens_new_conversation_never_reopens_closed(settings, conn) -> None:
    conversations = ConversationRepo(conn)
    old = conversations.create(
        Conversation(
            topic="kafka-prep",
            status=ConversationStatus.CLOSED,
            summary="Discussed studying Kafka fundamentals.",
        )
    )

    bedrock = FakeBedrock(
        reconstruction=_reconstruction("kafka-prep"),
        analysis=ConversationAnalysis(
            title="Kafka continued",
            summary="Resumed the Kafka topic.",
            topic="kafka-prep",
            memory_candidates=[],
            tasks=[],
        ),
    )
    inputs = iter([""])  # exit immediately once the reconstruction is printed
    outputs: list[str] = []

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    resumed = service.resume("kafka-prep")

    assert resumed.id != old.id
    assert resumed.topic == "kafka-prep"
    assert resumed.status == ConversationStatus.CLOSED

    stored_old = ConversationRepo(conn).get(old.id)
    assert stored_old.status == ConversationStatus.CLOSED
    assert stored_old.summary == "Discussed studying Kafka fundamentals."

    assert any(
        "You were studying Kafka fundamentals for interview prep." in line
        for line in outputs
    )


def test_resume_reconstruction_uses_only_stored_summaries_memories_tasks(settings, conn) -> None:
    conversations = ConversationRepo(conn)
    memories = MemoryRepo(conn)
    tasks = TaskRepo(conn)

    old = conversations.create(
        Conversation(
            topic="kafka-prep",
            status=ConversationStatus.CLOSED,
            summary="Discussed studying Kafka fundamentals.",
        )
    )
    memories.create(
        Memory(
            conversation_id=old.id,
            memory_type=MemoryType.GOAL,
            content="User wants to learn Kafka for system design interview prep.",
            topic="kafka-prep",
            importance=0.9,
        )
    )
    from heybrain.core.models import Task

    tasks.create(Task(conversation_id=old.id, title="Read the Kafka docs on partitions"))

    bedrock = FakeBedrock(reconstruction=_reconstruction("kafka-prep"))
    inputs = iter([""])

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=lambda _prompt: next(inputs),
        output_fn=lambda _line: None,
    )

    service.resume("kafka-prep")

    reconstruction_call = next(
        call for call in bedrock.structured_calls if call[2] is TopicReconstruction
    )
    system_prompt = reconstruction_call[1]
    assert "Discussed studying Kafka fundamentals." in system_prompt
    assert "User wants to learn Kafka for system design interview prep." in system_prompt
    assert "Read the Kafka docs on partitions" in system_prompt


def test_resume_raises_for_unknown_topic(settings, conn) -> None:
    service = AppService(conn=conn, settings=settings, bedrock=FakeBedrock())
    with pytest.raises(HeyBrainError):
        service.resume("a topic that was never discussed")


def test_resume_none_falls_back_to_most_recent_topic(settings, conn) -> None:
    conversations = ConversationRepo(conn)
    now = datetime.now(timezone.utc)
    conversations.create(
        Conversation(
            topic="older-topic",
            status=ConversationStatus.CLOSED,
            summary="An older thread.",
            created_at=now - timedelta(days=5),
            updated_at=now - timedelta(days=5),
        )
    )
    conversations.create(
        Conversation(
            topic="kafka-prep",
            status=ConversationStatus.CLOSED,
            summary="Discussed studying Kafka fundamentals.",
        )
    )

    bedrock = FakeBedrock(
        reconstruction=_reconstruction("kafka-prep"),
        analysis=ConversationAnalysis(
            title="Kafka continued",
            summary="Resumed the Kafka topic.",
            topic="kafka-prep",
            memory_candidates=[],
            tasks=[],
        ),
    )
    inputs = iter([""])

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=lambda _prompt: next(inputs),
        output_fn=lambda _line: None,
    )

    resumed = service.resume(None)

    # The resumed conversation is opened against the most recently touched
    # topic (kafka-prep); the close-time re-summarization (`analysis` above)
    # is what actually stamps the final `topic`, so it's pinned to match.
    assert resumed.topic == "kafka-prep"


# --- CLI fuzzy topic matching -------------------------------------------------


def test_resolve_topic_fuzzy_matches_substring() -> None:
    topics = ["Kafka learning plan", "vacation planning"]
    assert resolve_topic("kafka", topics) == "Kafka learning plan"


def test_resolve_topic_exact_match_case_insensitive() -> None:
    topics = ["Kafka learning plan", "vacation planning"]
    assert resolve_topic("KAFKA LEARNING PLAN", topics) == "Kafka learning plan"


def test_resolve_topic_close_typo_match() -> None:
    topics = ["Kafka learning plan", "vacation planning"]
    assert resolve_topic("kafak learning plan", topics) == "Kafka learning plan"


def test_resolve_topic_returns_none_when_nothing_close() -> None:
    topics = ["Kafka learning plan"]
    assert resolve_topic("quantum computing", topics) is None


def test_resolve_topic_empty_topics_returns_none() -> None:
    assert resolve_topic("kafka", []) is None
