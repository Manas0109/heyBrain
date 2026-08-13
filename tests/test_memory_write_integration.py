"""Integration tests for issue #9's write path wired into AppService/CLI.

Covers: `AppService.remember`, `AppService.reprocess`, and background
extraction on capture-intent turns in `AppService.think` (plan.md §9).
Bedrock is faked throughout; no test here talks to AWS.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from heybrain.bedrock.schemas import ConversationAnalysis, ConversationTurn, MemoryCandidate
from heybrain.cli.main import app
from heybrain.core.config import Settings
from heybrain.core.models import MemoryStatus, MemoryType
from heybrain.core.service import AppService
from heybrain.memory.extractor import _ExtractionResult
from heybrain.memory.service import DedupeVerdict
from heybrain.memory.vectors import VectorStore
from heybrain.storage.db import get_connection

VEC = [1.0, 0.0, 0.0, 0.0]


class FakeBedrock:
    def __init__(
        self,
        turns: list[ConversationTurn] | None = None,
        analysis: ConversationAnalysis | None = None,
        extraction_candidates: list[MemoryCandidate] | None = None,
    ) -> None:
        self._turns = list(turns or [])
        self._analysis = analysis
        self._extraction_candidates = extraction_candidates or []
        self.embeddings: dict[str, list[float]] = {}
        self.structured_schemas: list[type] = []

    def structured(self, messages, system, schema, effort, model=None):
        self.structured_schemas.append(schema)
        if schema is ConversationAnalysis:
            return self._analysis
        if schema is _ExtractionResult:
            return _ExtractionResult(memory_candidates=self._extraction_candidates)
        if schema is DedupeVerdict:
            return DedupeVerdict(verdict="separate")
        return self._turns.pop(0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embeddings.get(t, VEC) for t in texts]


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


def _analysis() -> ConversationAnalysis:
    return ConversationAnalysis(
        title="Kafka prep",
        summary="Discussed studying Kafka for interviews.",
        topic="kafka-prep",
        memory_candidates=[],
        tasks=[],
    )


def test_remember_bypasses_threshold_and_runs_dedup(settings, conn, vector_store) -> None:
    candidate = MemoryCandidate(
        content="User prefers backend over frontend.",
        memory_type="preference",
        importance=0.05,  # below IMPORTANCE_THRESHOLD; remember must ignore this
        topic="career",
    )
    bedrock = FakeBedrock(extraction_candidates=[candidate])

    service = AppService(
        conn=conn,
        settings=settings,
        vector_store=vector_store,
        bedrock=bedrock,
        input_fn=lambda _p: "",
        output_fn=lambda _l: None,
    )

    memory = service.remember("I prefer backend over frontend")

    assert memory.memory_type == MemoryType.PREFERENCE
    assert memory.importance == 1.0
    assert memory.status == MemoryStatus.ACTIVE


def test_think_capture_turn_extracts_memory_in_background(
    settings, conn, vector_store
) -> None:
    candidate = MemoryCandidate(
        content="User wants to learn Kafka for system design interview prep.",
        memory_type="goal",
        importance=0.8,
        topic="kafka",
    )
    bedrock = FakeBedrock(
        turns=[ConversationTurn(intent="capture", reply="Got it, noted.")],
        analysis=_analysis(),
        extraction_candidates=[candidate],
    )
    inputs = iter([""])

    service = AppService(
        conn=conn,
        settings=settings,
        vector_store=vector_store,
        bedrock=bedrock,
        input_fn=lambda _p: next(inputs),
        output_fn=lambda _l: None,
    )

    conversation = service.think("I want to learn Kafka for system design prep.")

    assert service.join_pending_extraction(timeout=5) is True
    memories = service._memories.list_all()
    assert len(memories) == 1
    assert memories[0].conversation_id == conversation.id
    assert memories[0].content == candidate.content


def test_think_question_only_turn_does_not_spawn_extraction(
    settings, conn, vector_store
) -> None:
    bedrock = FakeBedrock(
        turns=[ConversationTurn(intent="question", reply="I don't have that yet.")],
        analysis=_analysis(),
    )
    inputs = iter([""])

    service = AppService(
        conn=conn,
        settings=settings,
        vector_store=vector_store,
        bedrock=bedrock,
        input_fn=lambda _p: next(inputs),
        output_fn=lambda _l: None,
    )

    service.think("what did I say about Kafka last week?")

    assert service._extraction_thread is None
    assert service.join_pending_extraction() is True


def test_reprocess_reruns_extraction_for_existing_conversation(
    settings, conn, vector_store
) -> None:
    candidate = MemoryCandidate(
        content="User wants to learn Kafka for system design interview prep.",
        memory_type="goal",
        importance=0.8,
        topic="kafka",
    )
    bedrock = FakeBedrock(
        turns=[ConversationTurn(intent="capture", reply="noted")],
        analysis=_analysis(),
        extraction_candidates=[],  # no candidates during `think` itself
    )
    inputs = iter([""])

    service = AppService(
        conn=conn,
        settings=settings,
        vector_store=vector_store,
        bedrock=bedrock,
        input_fn=lambda _p: next(inputs),
        output_fn=lambda _l: None,
    )

    conversation = service.think("I want to learn Kafka for system design prep.")
    service.join_pending_extraction(timeout=5)
    assert service._memories.list_all() == []

    # Simulate the interrupted-extraction escape hatch: rerun with candidates
    # now available, as if `brain reprocess <id>` were invoked later.
    bedrock._extraction_candidates = [candidate]
    memories = service.reprocess(conversation.id)

    assert len(memories) == 1
    assert memories[0].content == candidate.content


def test_cli_remember_command(settings, conn, vector_store, monkeypatch) -> None:
    candidate = MemoryCandidate(
        content="User prefers backend over frontend.",
        memory_type="preference",
        importance=0.9,
        topic="career",
    )
    bedrock = FakeBedrock(extraction_candidates=[candidate])

    monkeypatch.setattr(
        "heybrain.cli.remember.AppService",
        lambda: AppService(
            conn=conn, settings=settings, vector_store=vector_store, bedrock=bedrock
        ),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["remember", "I prefer backend over frontend"])

    assert result.exit_code == 0
    assert "preference" in result.output
    assert "backend over frontend" in result.output
