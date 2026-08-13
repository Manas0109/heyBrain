"""Unit tests for AppService.think — the conversation loop.

Bedrock is faked via a stub with a `.structured()` method; no test here
talks to AWS. Voice is exercised separately in tests/test_audio.py /
tests/test_transcription.py, so these tests stick to the text path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heybrain.bedrock.schemas import ConversationAnalysis, ConversationTurn
from heybrain.core.config import Settings
from heybrain.core.models import ConversationStatus, Role
from heybrain.core.service import AppService
from heybrain.storage.db import get_connection


class FakeBedrock:
    def __init__(self, turns: list[ConversationTurn], analysis: ConversationAnalysis) -> None:
        self._turns = list(turns)
        self._analysis = analysis
        self.structured_calls: list[tuple[list[dict], str, type]] = []
        self.embed_calls: list[list[str]] = []

    def structured(self, messages, system, schema, effort, model=None):
        self.structured_calls.append((messages, system, schema))
        if schema is ConversationAnalysis:
            return self._analysis
        return self._turns.pop(0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [[0.0] * 4 for _ in texts]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(heybrain_home=tmp_path)


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "brain.db")
    yield connection
    connection.close()


def _analysis() -> ConversationAnalysis:
    return ConversationAnalysis(
        title="Kafka prep",
        summary="Discussed studying Kafka for interviews.",
        topic="kafka-prep",
        memory_candidates=[],
        tasks=[],
    )


def test_think_runs_turns_and_closes_with_summary(settings, conn) -> None:
    turns = [
        ConversationTurn(intent="capture", reply="Got it, noted."),
        ConversationTurn(intent="capture", reply="Makes sense."),
    ]
    bedrock = FakeBedrock(turns, _analysis())
    inputs = iter(["I should study Kafka before interviews.", ""])
    outputs: list[str] = []

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    conversation = service.think("I want to learn Kafka for system design prep.")

    assert conversation.status == ConversationStatus.CLOSED
    assert conversation.title == "Kafka prep"
    assert conversation.summary == "Discussed studying Kafka for interviews."
    assert conversation.topic == "kafka-prep"
    assert "Got it, noted." in outputs

    stored_conversation, messages = service.show_conversation(conversation.id)
    assert stored_conversation.status == ConversationStatus.CLOSED
    roles = [m.role for m in messages]
    assert roles == [Role.USER, Role.ASSISTANT, Role.USER, Role.ASSISTANT]


def test_think_never_sends_more_than_context_window(settings, conn) -> None:
    turns = [ConversationTurn(intent="capture", reply=f"ack {i}") for i in range(8)]
    bedrock = FakeBedrock(turns, _analysis())
    inputs = iter([f"message {i}" for i in range(7)] + [""])
    outputs: list[str] = []

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    service.think(None)

    for messages, _system, schema in bedrock.structured_calls:
        if schema is ConversationTurn:
            assert len(messages) <= 6


def test_think_retrieves_memories_for_question_intent(settings, conn) -> None:
    """Issue #10: question-shaped turns run real retrieval before the reply.

    The store is empty here (no memories seeded), so retrieval legitimately
    returns nothing -- what this asserts is that `_run_turn` actually calls
    into embed()/retrieval for a question turn, not that it finds a memory.
    """
    turns = [ConversationTurn(intent="question", reply="I don't have that yet.")]
    bedrock = FakeBedrock(turns, _analysis())
    inputs = iter([""])
    outputs: list[str] = []

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    service.think("what did I say about Kafka last week?")

    assert bedrock.embed_calls == [["what did I say about Kafka last week?"]]


def test_think_skips_retrieval_for_plain_capture_turn(settings, conn) -> None:
    turns = [ConversationTurn(intent="capture", reply="noted")]
    bedrock = FakeBedrock(turns, _analysis())
    inputs = iter([""])

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=lambda _prompt: next(inputs),
        output_fn=lambda _line: None,
    )

    service.think("idea: a CLI that turns spoken thoughts into memories")

    assert bedrock.embed_calls == []


def test_think_ctrl_c_saves_and_skips_extraction(settings, conn) -> None:
    bedrock = FakeBedrock([ConversationTurn(intent="capture", reply="noted")], _analysis())
    outputs: list[str] = []

    def raise_interrupt(_prompt: str) -> str:
        raise KeyboardInterrupt

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=raise_interrupt,
        output_fn=outputs.append,
    )

    conversation = service.think("first thought, then I get interrupted")

    assert conversation.status == ConversationStatus.CLOSED
    assert conversation.title is None
    assert conversation.summary is None
    assert not any(call[2] is ConversationAnalysis for call in bedrock.structured_calls)


def test_think_exit_word_ends_conversation(settings, conn) -> None:
    turns = [ConversationTurn(intent="capture", reply="ok")]
    bedrock = FakeBedrock(turns, _analysis())
    inputs = iter(["one more thing", "exit"])

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=lambda _prompt: next(inputs),
        output_fn=lambda _line: None,
    )

    conversation = service.think(None)

    assert conversation.status == ConversationStatus.CLOSED
    assert conversation.title == "Kafka prep"


def test_list_and_show_conversations(settings, conn) -> None:
    bedrock = FakeBedrock(
        [ConversationTurn(intent="capture", reply="noted")], _analysis()
    )
    inputs = iter([""])

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=lambda _prompt: next(inputs),
        output_fn=lambda _line: None,
    )

    conversation = service.think("a thought worth capturing")

    recent = service.list_conversations()
    assert conversation.id in [c.id for c in recent]

    fetched, messages = service.show_conversation(conversation.id)
    assert fetched.id == conversation.id
    assert len(messages) == 2
