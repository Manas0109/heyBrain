"""Pull durable memory candidates out of a conversation (plan.md §8.1, §9).

The extraction call is a separate structured-output request from the
conversation-summary call in core.service -- it exists purely to produce
MemoryCandidate rows, scored and topic-labelled, for memory.service to
filter/dedupe/persist.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict

from heybrain.bedrock.client import BedrockService
from heybrain.bedrock.prompts import memory_extraction_prompt
from heybrain.bedrock.schemas import MemoryCandidate
from heybrain.core.errors import HeyBrainError
from heybrain.core.models import Message

logger = logging.getLogger(__name__)

_EXTRACTION_SYSTEM = "Extract durable memory candidates from this conversation."


class _ExtractionResult(BaseModel):
    """Structured-output wrapper -- `structured()` needs a top-level model."""

    model_config = ConfigDict(extra="forbid")

    memory_candidates: list[MemoryCandidate]


def extract_candidates(
    bedrock: BedrockService, messages: list[Message]
) -> list[MemoryCandidate]:
    """Run `memory_extraction_prompt` over a transcript, returning candidates.

    Never raises: extraction failure must not fail the conversation that
    produced it (the conversation is already saved by the time this runs,
    plan.md §9) -- a Bedrock error is logged and treated as "nothing found".
    """
    if not messages:
        return []

    conversation_text = "\n".join(f"{m.role.value}: {m.content}" for m in messages)
    prompt = memory_extraction_prompt(conversation_text=conversation_text)

    try:
        result = bedrock.structured(
            [{"role": "user", "content": prompt}],
            system=_EXTRACTION_SYSTEM,
            schema=_ExtractionResult,
            effort="low",
        )
    except HeyBrainError:
        logger.exception("memory extraction failed; treating as no candidates")
        return []

    return result.memory_candidates
