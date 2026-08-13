"""Usage logging for Bedrock calls.

Every BedrockService call writes one row to the `usage` table via
UsageRepo. Only token counts and latency are recorded here — never
conversation content.
"""

from __future__ import annotations

from heybrain.core.models import UsageRecord
from heybrain.storage.repositories import UsageRepo


def record_usage(
    usage_repo: UsageRepo,
    *,
    request_id: str,
    operation: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
) -> UsageRecord:
    record = UsageRecord(
        request_id=request_id,
        operation=operation,
        model_id=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
    )
    return usage_repo.create(record)
