"""Memory write path: extraction, scoring, deduplication (plan.md §8.1, §8.2).

The hardest and highest-risk piece of the build. Every candidate that
survives the importance filter is embedded and checked against the nearest
existing memory; near-duplicates go through an LLM verdict (merge /
supersede / separate) instead of being inserted blind. Without this the
memory store degenerates into near-duplicate chatter (plan.md §8.2).

Write ordering on every branch: the Chroma vector is written before the
SQLite row. A crash between the two leaves an orphan vector, which is
harmless -- `brain reindex` (issue #8) rebuilds Chroma from SQLite and
drops it. The reverse order would leave an *active* SQLite row with no
vector, which is silently invisible to future dedup searches and is how
the store re-degenerates into duplicates -- the one thing this module
exists to prevent.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict

from heybrain.bedrock.client import BedrockService
from heybrain.bedrock.prompts import dedupe_verdict_prompt
from heybrain.bedrock.schemas import MemoryCandidate
from heybrain.core.errors import HeyBrainError
from heybrain.core.models import Memory, MemoryStatus, MemoryType, Message, Role
from heybrain.memory.extractor import extract_candidates
from heybrain.memory.vectors import VectorStore, memory_metadata
from heybrain.storage.repositories import MemoryRepo, MessageRepo

logger = logging.getLogger(__name__)

# plan.md §8.1 -- candidates below this importance are dropped automatically.
# `brain remember` bypasses this (stores at importance 1.0) but still runs
# the dedup pipeline below.
IMPORTANCE_THRESHOLD = 0.6

# plan.md §8.2 -- candidates at/above this cosine similarity to the nearest
# existing memory are sent to the LLM for a merge/supersede/separate verdict
# instead of being inserted directly.
DEDUPE_SIMILARITY_THRESHOLD = 0.90

_DEDUPE_SYSTEM = (
    "Resolve how a new memory candidate relates to a near-duplicate existing "
    "memory. If merging, write the combined statement as self-contained "
    "third-person text, not a quote."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DedupeVerdict(BaseModel):
    """Structured-output schema for `dedupe_verdict_prompt`."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["merge", "supersede", "separate"]
    # Only meaningful when verdict == "merge"; the model's combined statement.
    merged_content: str | None = None


class MemoryService:
    def __init__(
        self,
        *,
        bedrock: BedrockService,
        vector_store: VectorStore,
        memory_repo: MemoryRepo,
        message_repo: MessageRepo,
    ) -> None:
        self._bedrock = bedrock
        self._vector_store = vector_store
        self._memories = memory_repo
        self._messages = message_repo

    def process_conversation(self, conversation_id: str) -> list[Memory]:
        """Extract, filter, and write memories for a conversation.

        Never raises: this runs after the conversation is already saved
        (often on a background thread), so a failure here must not lose or
        corrupt anything the user already has (plan.md §9).
        """
        try:
            messages = self._messages.list_by_conversation(conversation_id)
            candidates = extract_candidates(self._bedrock, messages)
        except Exception:
            logger.exception(
                "memory extraction failed for conversation %s", conversation_id
            )
            return []

        stored: list[Memory] = []
        for candidate in candidates:
            if candidate.importance < IMPORTANCE_THRESHOLD:
                continue
            try:
                stored.append(self.write_candidate(candidate, conversation_id))
            except Exception:
                logger.exception(
                    "failed to write memory candidate for conversation %s",
                    conversation_id,
                )
        return stored

    def remember(self, text: str, conversation_id: str) -> Memory:
        """`brain remember` -- bypass the importance threshold, run dedup.

        Classifies `text` the same way extraction would (so it gets a real
        memory_type/topic, not a guess), then forces importance to 1.0
        regardless of what came back.
        """
        pseudo_message = Message(
            conversation_id=conversation_id, role=Role.USER, content=text
        )
        candidates = extract_candidates(self._bedrock, [pseudo_message])
        if candidates:
            candidate = candidates[0]
        else:
            candidate = MemoryCandidate(
                content=text, memory_type="fact", topic="general", importance=1.0
            )
        candidate = candidate.model_copy(update={"importance": 1.0})
        return self.write_candidate(candidate, conversation_id)

    def write_candidate(self, candidate: MemoryCandidate, conversation_id: str) -> Memory:
        """Embed a candidate, dedupe it against the nearest memory, persist it.

        Bedrock/storage failures here propagate (the caller decides whether
        that's fatal); the dedupe *verdict* call specifically degrades to
        'separate' on failure rather than raising, so a flaky LLM call can
        never cause a memory to be dropped.
        """
        embedding = self._bedrock.embed([candidate.content])[0]

        nearest = self._vector_store.search(embedding, k=1)
        if nearest:
            nearest_id, distance = nearest[0]
            similarity = 1.0 - distance
            if similarity >= DEDUPE_SIMILARITY_THRESHOLD:
                existing = self._memories.get(nearest_id)
                if existing is not None:
                    return self._apply_verdict(
                        candidate, embedding, existing, conversation_id
                    )

        return self._insert_new(candidate, conversation_id, embedding)

    def _apply_verdict(
        self,
        candidate: MemoryCandidate,
        embedding: list[float],
        existing: Memory,
        conversation_id: str,
    ) -> Memory:
        verdict = self._get_verdict(existing, candidate)

        if verdict.verdict == "merge":
            existing.content = verdict.merged_content or candidate.content
            existing.updated_at = _now()
            self._vector_store.upsert(existing.id, embedding, memory_metadata(existing))
            return self._memories.update(existing)

        if verdict.verdict == "supersede":
            new_memory = self._insert_new(candidate, conversation_id, embedding)
            existing.status = MemoryStatus.SUPERSEDED
            existing.updated_at = _now()
            self._memories.update(existing)
            self._vector_store.delete(existing.id)
            return new_memory

        # separate -- genuinely different facts; keep both.
        return self._insert_new(candidate, conversation_id, embedding)

    def _get_verdict(self, existing: Memory, candidate: MemoryCandidate) -> DedupeVerdict:
        prompt = dedupe_verdict_prompt(
            existing_memory=existing.content, candidate_memory=candidate.content
        )
        try:
            return self._bedrock.structured(
                [{"role": "user", "content": prompt}],
                system=_DEDUPE_SYSTEM,
                schema=DedupeVerdict,
                effort="low",
            )
        except HeyBrainError:
            logger.exception("dedupe verdict failed; defaulting to separate")
            return DedupeVerdict(verdict="separate", merged_content=None)

    def _insert_new(
        self, candidate: MemoryCandidate, conversation_id: str, embedding: list[float]
    ) -> Memory:
        memory = Memory(
            conversation_id=conversation_id,
            memory_type=MemoryType(candidate.memory_type),
            content=candidate.content,
            topic=candidate.topic,
            importance=candidate.importance,
        )
        self._vector_store.upsert(memory.id, embedding, memory_metadata(memory))
        return self._memories.create(memory)
