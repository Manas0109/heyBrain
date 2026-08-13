"""Unit tests for BedrockService.

These replay recorded Converse/invoke_model JSON fixtures through a fake
bedrock-runtime client injected via chat_model_factory/embeddings_model_factory
— no test in this file talks to AWS.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from langchain_aws import BedrockEmbeddings, ChatBedrockConverse

from heybrain.bedrock import client as bedrock_client
from heybrain.bedrock.client import BedrockService
from heybrain.bedrock.schemas import ConversationAnalysis
from heybrain.core.config import Settings
from heybrain.core.errors import BedrockError
from heybrain.storage.db import get_connection
from heybrain.storage.repositories import UsageRepo

FIXTURES = Path(__file__).parent / "fixtures" / "bedrock"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class _FakeStreamingBody:
    def __init__(self, payload: dict) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._data


class FakeBotoClient:
    """Fake bedrock-runtime client that replays queued fixtures in order."""

    def __init__(self) -> None:
        self._converse_queue: list[Any] = []
        self._invoke_model_queue: list[Any] = []
        self.converse_calls: list[dict] = []
        self.invoke_model_calls: list[dict] = []

    def queue_converse(self, item: dict | Exception) -> None:
        self._converse_queue.append(item)

    def queue_invoke_model(self, item: dict | Exception) -> None:
        self._invoke_model_queue.append(item)

    def converse(self, **kwargs: Any) -> dict:
        self.converse_calls.append(kwargs)
        item = self._converse_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return copy.deepcopy(item)

    def invoke_model(self, **kwargs: Any) -> dict:
        self.invoke_model_calls.append(kwargs)
        item = self._invoke_model_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        response = copy.deepcopy(item)
        body = response.pop("body")
        return {**response, "body": _FakeStreamingBody(body)}


def _throttling_error() -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"},
            "ResponseMetadata": {"HTTPStatusCode": 429},
        },
        "Converse",
    )


def _validation_error() -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "ValidationException", "Message": "Bad request"},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        "Converse",
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bedrock_client.time, "sleep", lambda *_: None)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(heybrain_home=tmp_path)


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "brain.db")
    yield connection
    connection.close()


@pytest.fixture
def usage_repo(conn) -> UsageRepo:
    return UsageRepo(conn)


def _service(settings: Settings, usage_repo: UsageRepo, fake_client: FakeBotoClient) -> BedrockService:
    def chat_model_factory(model_id: str, effort: str, output_config: dict | None):
        # `effort` is intentionally not forwarded as `reasoning_effort` --
        # matching bedrock/client.py's real factory (plan.md §6.3): the
        # configured chat model doesn't support reasoning-effort routing.
        kwargs: dict[str, Any] = dict(
            model_id=model_id,
            client=fake_client,
            bedrock_client=fake_client,
            max_tokens=bedrock_client.CHAT_MAX_TOKENS,
            timeout=bedrock_client.CHAT_TIMEOUT_SECONDS,
        )
        if output_config is not None:
            kwargs["output_config"] = output_config
        return ChatBedrockConverse(**kwargs)

    def embeddings_model_factory():
        return BedrockEmbeddings(
            model_id=settings.bedrock_embedding_model_id, client=fake_client
        )

    return BedrockService(
        usage_repo,
        settings,
        chat_model_factory=chat_model_factory,
        embeddings_model_factory=embeddings_model_factory,
    )


def test_chat_returns_text_and_logs_usage(settings, usage_repo) -> None:
    fake_client = FakeBotoClient()
    fake_client.queue_converse(_load("chat_turn.json"))
    service = _service(settings, usage_repo, fake_client)

    result = service.chat(
        [{"role": "user", "content": "What did we decide?"}],
        system="You are a helpful assistant.",
        effort="medium",
    )

    assert result == "Sure — here's a quick recap of what we covered."
    assert len(fake_client.converse_calls) == 1

    rows = usage_repo.list_by_request("req-chat-001")
    assert len(rows) == 1
    assert rows[0].operation == "chat"
    assert rows[0].input_tokens == 42
    assert rows[0].output_tokens == 18


def test_chat_retries_on_throttling_then_succeeds(settings, usage_repo) -> None:
    fake_client = FakeBotoClient()
    fake_client.queue_converse(_throttling_error())
    fake_client.queue_converse(_load("chat_turn.json"))
    service = _service(settings, usage_repo, fake_client)

    result = service.chat([{"role": "user", "content": "hi"}], system="sys", effort="low")

    assert result == "Sure — here's a quick recap of what we covered."
    assert len(fake_client.converse_calls) == 2


def test_chat_gives_up_after_max_attempts(settings, usage_repo) -> None:
    fake_client = FakeBotoClient()
    fake_client.queue_converse(_throttling_error())
    fake_client.queue_converse(_throttling_error())
    fake_client.queue_converse(_throttling_error())
    service = _service(settings, usage_repo, fake_client)

    with pytest.raises(BedrockError):
        service.chat([{"role": "user", "content": "hi"}], system="sys", effort="low")

    assert len(fake_client.converse_calls) == 3


def test_chat_does_not_retry_non_retryable_errors(settings, usage_repo) -> None:
    fake_client = FakeBotoClient()
    fake_client.queue_converse(_validation_error())
    service = _service(settings, usage_repo, fake_client)

    with pytest.raises(BedrockError):
        service.chat([{"role": "user", "content": "hi"}], system="sys", effort="low")

    assert len(fake_client.converse_calls) == 1


def test_structured_parses_valid_response(settings, usage_repo) -> None:
    fake_client = FakeBotoClient()
    fake_client.queue_converse(_load("structured_analysis.json"))
    service = _service(settings, usage_repo, fake_client)

    result = service.structured(
        [{"role": "user", "content": "Summarize this conversation."}],
        system="Extract structured data.",
        schema=ConversationAnalysis,
        effort="low",
    )

    assert isinstance(result, ConversationAnalysis)
    assert result.title == "Planning the Q3 roadmap"
    assert len(result.memory_candidates) == 1
    assert len(fake_client.converse_calls) == 1


def test_structured_repairs_once_then_succeeds(settings, usage_repo) -> None:
    fake_client = FakeBotoClient()
    fake_client.queue_converse(_load("structured_malformed.json"))
    fake_client.queue_converse(_load("structured_repaired.json"))
    service = _service(settings, usage_repo, fake_client)

    result = service.structured(
        [{"role": "user", "content": "Summarize this conversation."}],
        system="Extract structured data.",
        schema=ConversationAnalysis,
        effort="low",
    )

    assert isinstance(result, ConversationAnalysis)
    assert result.title == "Planning the Q3 roadmap"
    assert len(fake_client.converse_calls) == 2

    repair_rows = usage_repo.list_by_request("req-struct-repair-001")
    assert len(repair_rows) == 1
    assert repair_rows[0].operation == "structured:ConversationAnalysis:repair"


def test_structured_raises_recoverable_error_after_failed_repair(settings, usage_repo) -> None:
    fake_client = FakeBotoClient()
    fake_client.queue_converse(_load("structured_malformed.json"))
    fake_client.queue_converse(_load("structured_still_malformed.json"))
    service = _service(settings, usage_repo, fake_client)

    with pytest.raises(BedrockError) as exc_info:
        service.structured(
            [{"role": "user", "content": "Summarize this conversation."}],
            system="Extract structured data.",
            schema=ConversationAnalysis,
            effort="low",
        )

    assert exc_info.value.recoverable is True
    assert len(fake_client.converse_calls) == 2


def test_embed_returns_vectors_and_logs_usage(settings, usage_repo, conn) -> None:
    fake_client = FakeBotoClient()
    fake_client.queue_invoke_model(_load("embed_response.json"))
    service = _service(settings, usage_repo, fake_client)

    vectors = service.embed(["hello world"])

    assert vectors == [[0.011, -0.023, 0.045, 0.007]]
    assert len(fake_client.invoke_model_calls) == 1

    rows = conn.execute("SELECT * FROM usage WHERE operation = 'embed'").fetchall()
    assert len(rows) == 1
    assert rows[0]["model_id"] == settings.bedrock_embedding_model_id


def test_embed_empty_list_short_circuits(settings, usage_repo) -> None:
    fake_client = FakeBotoClient()
    service = _service(settings, usage_repo, fake_client)

    assert service.embed([]) == []
    assert fake_client.invoke_model_calls == []
