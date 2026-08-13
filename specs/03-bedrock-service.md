# Bedrock Service (issue #3)

`BedrockService` (`bedrock/client.py`) is the only module in heyBrain that
touches `botocore`/`langchain_aws`. It wraps every model call — chat turns,
structured extraction, and embeddings — behind three methods, so callers
work with plain strings, Pydantic models, and `BedrockError` only.

## Public API

### `bedrock/client.py` — `BedrockService`

```python
BedrockService(usage_repo: UsageRepo, settings: Settings | None = None)
```

- `chat(messages: list[dict[str, str]], system: str, effort: EffortLevel, model: str | None = None) -> str`
  One conversational turn. `messages` are `{"role": "user"|"assistant", "content": str}`. Returns the assistant's reply text.
- `structured(messages: list[dict[str, str]], system: str, schema: type[BaseModel], effort: EffortLevel, model: str | None = None) -> BaseModel`
  Same inputs as `chat`, plus a Pydantic `schema`. Returns a validated instance of `schema`.
- `embed(texts: list[str]) -> list[list[float]]`
  Returns one embedding vector per input text, same order. Empty input returns `[]`.

`EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]`. `model` defaults to `settings.bedrock_model_id` when omitted.

### `bedrock/usage.py`

- `record_usage(usage_repo: UsageRepo, *, request_id: str, operation: str, model_id: str, input_tokens: int, output_tokens: int, latency_ms: int) -> UsageRecord`
  Writes one `usage` row. Called internally by every `BedrockService` method — callers never call this directly.

## Constraints other agents must respect

- **Never send `temperature`, `top_p`, `top_k`, or `budget_tokens`** to Bedrock — not supported on the current model surface.
- **Effort is accepted but not currently forwarded to the model.** `chat()`/`structured()` require an `effort` argument for call-site intent and usage-log bookkeeping, but the presently-configured chat model does not support `reasoning_effort` routing, so it is not sent on the wire. Pass `effort` per plan.md §6.4's routing table anyway (intent classification → fast model/`low`, conversation turn → primary/`medium`, memory extraction → primary/`low`, recall synthesis → primary/`medium`, topic reconstruction → primary/`medium`, reminder extraction → fast model/`low`) so routing is correct if/when the configured model changes.
- **No assistant-turn prefills.** `structured()` uses Bedrock's native `output_config.textFormat=json_schema` output mode, never a pre-filled assistant message.
- **Retries:** exponential backoff + jitter, max 3 attempts total, only on throttling, 5xx, or timeout errors. Any other error fails immediately, no retry.
- **Timeouts:** 30s for `chat`/`structured`, 10s for `embed`.
- **Errors:** every `botocore`/`langchain_aws` exception is translated into `BedrockError` before it leaves this module — callers never see provider exceptions directly. A structured-output validation failure gets exactly one repair retry; if that also fails, `BedrockError` is raised with `.recoverable = True` so callers can degrade gracefully instead of crashing.
- **Usage logging:** one `usage` row is written per call (chat, structured, structured-repair, embed) via `UsageRepo`. Only token counts, latency, and IDs are logged — conversation content is never written to the `usage` table.

## Manual verification

`scripts/bedrock_smoke.py` is a live smoke test (not run in CI) exercising a chat turn, a structured extraction against `ConversationAnalysis`, and an embedding call against real Bedrock credentials:

```
python scripts/bedrock_smoke.py
```

Requires `AWS_REGION`/`AWS_PROFILE` (or the default AWS credential chain) configured, and the models in `.env`/`core/config.py` enabled in the target region.
