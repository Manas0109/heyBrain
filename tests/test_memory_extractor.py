"""Unit tests for memory.extractor.extract_candidates (issue #9).

Bedrock is faked; no test here talks to AWS.
"""

from __future__ import annotations

import pytest

from heybrain.bedrock.schemas import MemoryCandidate
from heybrain.core.errors import BedrockError
from heybrain.core.models import Message, Role
from heybrain.memory.extractor import _ExtractionResult, extract_candidates


class FakeBedrock:
    def __init__(self, result: _ExtractionResult | Exception) -> None:
        self._result = result
        self.calls: list[tuple[list[dict], str, type]] = []

    def structured(self, messages, system, schema, effort, model=None):
        self.calls.append((messages, system, schema))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _message(content: str) -> Message:
    return Message(conversation_id="conv-1", role=Role.USER, content=content)


def test_extract_candidates_returns_parsed_candidates() -> None:
    candidate = MemoryCandidate(
        content="User wants to learn Kafka for system design interview prep.",
        memory_type="goal",
        importance=0.8,
        topic="kafka",
    )
    bedrock = FakeBedrock(_ExtractionResult(memory_candidates=[candidate]))

    result = extract_candidates(bedrock, [_message("I want to learn Kafka.")])

    assert result == [candidate]
    assert len(bedrock.calls) == 1
    _messages, _system, schema = bedrock.calls[0]
    assert schema is _ExtractionResult


def test_extract_candidates_empty_conversation_skips_bedrock_call() -> None:
    bedrock = FakeBedrock(_ExtractionResult(memory_candidates=[]))

    result = extract_candidates(bedrock, [])

    assert result == []
    assert bedrock.calls == []


def test_extract_candidates_bedrock_failure_returns_empty_list() -> None:
    bedrock = FakeBedrock(BedrockError("boom", recoverable=True))

    result = extract_candidates(bedrock, [_message("anything")])

    assert result == []
