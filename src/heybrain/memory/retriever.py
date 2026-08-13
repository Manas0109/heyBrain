"""Memory read path: retrieval and ranking (plan.md §8.4).

```
query → embed → Chroma top-K=8 (status=active) → rerank → top 5 → SQLite
```

Chroma only returns ids and distances, so ranking needs each candidate's
importance and age from SQLite. Rather than a fetch-then-refetch round
trip, the 8 Chroma candidates are pulled from SQLite in one `get_many`
call, ranked in Python, and sliced down to `k` -- one Chroma call and one
SQLite call, no N+1 (issue #10's technical requirement).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from heybrain.bedrock.client import BedrockService
from heybrain.core.models import Memory, MemoryStatus
from heybrain.memory.vectors import VectorStore
from heybrain.storage.repositories import MemoryRepo

# plan.md §8.4 -- Chroma is over-fetched at this width so the rerank step
# (similarity x importance x recency) has room to reorder before truncating
# to the final top-K returned to callers.
SEARCH_K = 8

# Exponential decay: a memory's recency weight halves every RECENCY_HALF_LIFE
# hours old it is. weight = 0.5 ** (age_hours / RECENCY_HALF_LIFE_HOURS).
# One week means a memory from earlier today is barely discounted, while one
# from a month ago contributes little unless it's unusually important or
# similar -- tuned for a personal-memory app, not a news feed.
RECENCY_HALF_LIFE_HOURS = 24 * 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def recency_decay(created_at: datetime, *, now: datetime | None = None) -> float:
    """Exponential recency weight in (0, 1], 1.0 for a memory created now."""
    reference = now if now is not None else _now()
    age_hours = max(0.0, (reference - created_at).total_seconds() / 3600.0)
    return math.pow(0.5, age_hours / RECENCY_HALF_LIFE_HOURS)


def _rank_score(memory: Memory, similarity: float, *, now: datetime) -> float:
    return similarity * memory.importance * recency_decay(memory.created_at, now=now)


class MemoryRetriever:
    def __init__(
        self,
        *,
        bedrock: BedrockService,
        vector_store: VectorStore,
        memory_repo: MemoryRepo,
    ) -> None:
        self._bedrock = bedrock
        self._vector_store = vector_store
        self._memories = memory_repo

    def retrieve(self, query: str, k: int = 5) -> list[Memory]:
        """Top-`k` active memories relevant to `query`, ranked by
        similarity x importance x recency (plan.md §8.4).

        Returns `[]` cleanly on a fresh install with no memories stored --
        no Chroma or SQLite call assumes the store is non-empty.
        """
        embedding = self._bedrock.embed([query])[0]
        results = self._vector_store.search(embedding, k=SEARCH_K)
        if not results:
            return []

        ids = [memory_id for memory_id, _ in results]
        similarity_by_id = {
            memory_id: 1.0 - distance for memory_id, distance in results
        }

        candidates = self._memories.get_many(ids)
        now = _now()
        ranked = sorted(
            candidates,
            key=lambda memory: _rank_score(
                memory, similarity_by_id[memory.id], now=now
            ),
            reverse=True,
        )
        return ranked[:k]

    def retrieve_by_topic(self, topic: str) -> list[Memory]:
        """Active memories for `topic`, most recent first (for `brain resume`, issue #12)."""
        return [
            memory
            for memory in self._memories.list_by_topic(topic)
            if memory.status == MemoryStatus.ACTIVE
        ]
