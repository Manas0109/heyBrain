"""BedrockService — the only module that touches botocore/langchain_aws.

Every model call (chat, structured extraction, embeddings) goes through
here so the rest of the app only ever sees plain strings, Pydantic
models, and BedrockError. See plan.md §6 for the request rules this
module exists to enforce:

- Never send temperature/top_p/top_k (removed on current Claude models).
- Never send budget_tokens or `reasoning_effort` — the configured chat
  model (Qwen) doesn't support reasoning-effort routing, so `effort` is
  accepted for call-site intent and usage-log bookkeeping only and is
  never forwarded to the model.
- Never use assistant-turn prefills to force JSON; structured() uses
  Bedrock's native `output_config.textFormat=json_schema` output mode.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from typing import Any, Callable, Literal

from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from heybrain.bedrock.usage import record_usage
from heybrain.core.config import Settings, get_settings
from heybrain.core.errors import BedrockError
from heybrain.storage.repositories import UsageRepo

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]

CHAT_TIMEOUT_SECONDS = 30
EMBED_TIMEOUT_SECONDS = 10
CHAT_MAX_TOKENS = 4096

_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 0.5

_RETRYABLE_ERROR_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "ModelNotReadyException",
    "ModelTimeoutException",
    "InternalServerException",
}
_TIMEOUT_ERRORS = (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError)

_REPAIR_INSTRUCTION = (
    "That response was not valid JSON matching the required schema. "
    "Reply again with only JSON that satisfies the schema — no prose, "
    "no markdown fences."
)


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, _TIMEOUT_ERRORS):
        return True
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "")
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return code in _RETRYABLE_ERROR_CODES or status >= 500
    return False


def _invoke_with_retries(fn: Callable[[], Any]) -> Any:
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as error:
            if attempt >= _MAX_ATTEMPTS or not _is_retryable(error):
                raise BedrockError(f"Bedrock request failed: {error}") from error
            delay = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            time.sleep(delay + random.uniform(0, _BASE_BACKOFF_SECONDS))


def _to_langchain_messages(messages: list[dict[str, str]]) -> list[BaseMessage]:
    result: list[BaseMessage] = []
    for message in messages:
        role = message["role"]
        content = message["content"]
        if role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
        else:
            raise BedrockError(f"Unsupported message role: {role!r}")
    return result


def _text_of(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _json_schema_output_config(schema: type[BaseModel]) -> dict[str, Any]:
    json_schema = schema.model_json_schema()
    return {
        "textFormat": {
            "type": "json_schema",
            "structure": {
                "jsonSchema": {
                    "schema": json.dumps(json_schema, ensure_ascii=False),
                    "name": schema.__name__,
                    "description": schema.__doc__ or schema.__name__,
                }
            },
        }
    }


def _parse_structured(message: AIMessage, schema: type[BaseModel]) -> BaseModel | None:
    try:
        return schema.model_validate_json(_text_of(message))
    except ValidationError:
        return None


class BedrockService:
    """Owns every Bedrock model call. See module docstring."""

    def __init__(
        self,
        usage_repo: UsageRepo,
        settings: Settings | None = None,
        *,
        chat_model_factory: Callable[[str, EffortLevel, dict | None], Any] | None = None,
        embeddings_model_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._usage_repo = usage_repo
        self._settings = settings or get_settings()
        self._chat_model_factory = chat_model_factory or self._default_chat_model
        self._embeddings_model_factory = (
            embeddings_model_factory or self._default_embeddings_model
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        system: str,
        effort: EffortLevel,
        model: str | None = None,
    ) -> str:
        model_id = model or self._settings.bedrock_model_id
        llm = self._chat_model_factory(model_id, effort, None)
        lc_messages = [SystemMessage(content=system), *_to_langchain_messages(messages)]

        ai_message, latency_ms = self._invoke(llm, lc_messages)
        self._log_usage(
            ai_message, operation="chat", model_id=model_id, latency_ms=latency_ms
        )
        return _text_of(ai_message)

    def structured(
        self,
        messages: list[dict[str, str]],
        system: str,
        schema: type[BaseModel],
        effort: EffortLevel,
        model: str | None = None,
    ) -> BaseModel:
        model_id = model or self._settings.bedrock_model_id
        output_config = _json_schema_output_config(schema)
        llm = self._chat_model_factory(model_id, effort, output_config)
        lc_messages = [SystemMessage(content=system), *_to_langchain_messages(messages)]
        operation = f"structured:{schema.__name__}"

        ai_message, latency_ms = self._invoke(llm, lc_messages)
        self._log_usage(
            ai_message, operation=operation, model_id=model_id, latency_ms=latency_ms
        )
        parsed = _parse_structured(ai_message, schema)
        if parsed is not None:
            return parsed

        repair_messages = [
            *lc_messages,
            AIMessage(content=_text_of(ai_message)),
            HumanMessage(content=_REPAIR_INSTRUCTION),
        ]
        repair_message, repair_latency_ms = self._invoke(llm, repair_messages)
        self._log_usage(
            repair_message,
            operation=f"{operation}:repair",
            model_id=model_id,
            latency_ms=repair_latency_ms,
        )
        parsed = _parse_structured(repair_message, schema)
        if parsed is not None:
            return parsed

        raise BedrockError(
            f"Bedrock returned invalid structured output for {schema.__name__} "
            "after one repair retry",
            recoverable=True,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        embeddings_model = self._embeddings_model_factory()
        started = time.monotonic()
        vectors = _invoke_with_retries(lambda: embeddings_model.embed_documents(texts))
        latency_ms = int((time.monotonic() - started) * 1000)

        # BedrockEmbeddings discards Titan's per-call request id/token count when
        # returning plain vectors, so usage is logged with a locally generated id.
        record_usage(
            self._usage_repo,
            request_id=uuid.uuid4().hex,
            operation="embed",
            model_id=self._settings.bedrock_embedding_model_id,
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
        )
        return vectors

    def _invoke(
        self, llm: Any, lc_messages: list[BaseMessage]
    ) -> tuple[AIMessage, int]:
        started = time.monotonic()
        ai_message = _invoke_with_retries(lambda: llm.invoke(lc_messages))
        latency_ms = int((time.monotonic() - started) * 1000)
        return ai_message, latency_ms

    def _log_usage(
        self, ai_message: AIMessage, *, operation: str, model_id: str, latency_ms: int
    ) -> None:
        usage = ai_message.usage_metadata or {}
        response_metadata = ai_message.response_metadata or {}
        request_id = (
            response_metadata.get("ResponseMetadata", {}).get("RequestId")
            or uuid.uuid4().hex
        )
        record_usage(
            self._usage_repo,
            request_id=request_id,
            operation=operation,
            model_id=model_id,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            latency_ms=latency_ms,
        )

    def _default_chat_model(
        self, model_id: str, effort: EffortLevel, output_config: dict | None
    ) -> ChatBedrockConverse:
        # `effort` is intentionally not forwarded: the configured chat model
        # (Qwen) doesn't support reasoning-effort routing, and sending
        # `reasoning_effort` to it only produces a langchain_aws warning.
        kwargs: dict[str, Any] = dict(
            model_id=model_id,
            region_name=self._settings.aws_region,
            max_tokens=CHAT_MAX_TOKENS,
            timeout=CHAT_TIMEOUT_SECONDS,
        )
        if self._settings.aws_profile:
            kwargs["credentials_profile_name"] = self._settings.aws_profile
        if output_config is not None:
            kwargs["output_config"] = output_config
        return ChatBedrockConverse(**kwargs)

    def _default_embeddings_model(self) -> BedrockEmbeddings:
        kwargs: dict[str, Any] = dict(
            model_id=self._settings.bedrock_embedding_model_id,
            region_name=self._settings.aws_region,
            config=Config(
                connect_timeout=EMBED_TIMEOUT_SECONDS,
                read_timeout=EMBED_TIMEOUT_SECONDS,
            ),
        )
        if self._settings.aws_profile:
            kwargs["credentials_profile_name"] = self._settings.aws_profile
        return BedrockEmbeddings(**kwargs)
