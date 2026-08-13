# Issue #10 — Memory read path: retrieval and ranking

## Purpose

`memory/retriever.py` (`MemoryRetriever`) turns a natural-language query into a ranked list of the user's most relevant, currently-active long-term memories. It is the read counterpart to issue #9's write path: given a query, it embeds it, does one semantic search against Chroma, reranks the candidates by relevance × importance × recency, and returns full `Memory` rows from SQLite in that rank order.

## Public API

```python
class MemoryRetriever:
    def __init__(
        self,
        *,
        bedrock: BedrockService,
        vector_store: VectorStore,
        memory_repo: MemoryRepo,
    ) -> None: ...

    def retrieve(self, query: str, k: int = 5) -> list[Memory]: ...

    def retrieve_by_topic(self, topic: str) -> list[Memory]: ...
```

- `retrieve(query, k=5)` — top-`k` active memories relevant to `query`, ranked highest-score-first. Returns `[]` if the store has no active matches.
- `retrieve_by_topic(topic)` — active memories with `topic == topic`, most-recent-first (exact match, no embedding/Chroma call). Intended for issue #12's `brain resume` flow.

## Ranking pipeline (`retrieve`)

```
query
  → BedrockService.embed([query])[0]
  → VectorStore.search(embedding, k=SEARCH_K=8)   # Chroma, status="active" filter built into VectorStore
  → [] short-circuit if no results
  → MemoryRepo.get_many(ids)                       # one SQLite call for all 8 candidates
  → sort by similarity × importance × recency_decay, descending
  → return top k (default 5)
```

- `similarity = 1.0 - distance` (Chroma collection uses `hnsw:space=cosine`, so `distance` is already `1 - cosine_similarity`).
- `importance` is the memory's stored `importance` field (0.0–1.0), set at write time (issue #9).
- `recency_decay(created_at)` — exponential half-life decay:
  ```
  recency_decay = 0.5 ** (age_hours / RECENCY_HALF_LIFE_HOURS)
  ```
  `RECENCY_HALF_LIFE_HOURS = 24 * 7` (one week): a memory's recency weight halves every 7 days it ages. `age_hours` is clamped to ≥ 0.

`SEARCH_K = 8` is over-fetched from Chroma so the rerank step has room to reorder before truncating to the caller's requested `k`.

## Constraints

- **Exactly one Chroma call + one SQLite call per `retrieve()`** — no N+1. The 8 Chroma candidates are pulled from SQLite in a single `MemoryRepo.get_many(ids)` call; there is no per-candidate refetch.
- **Clean empty-store behavior** — if `VectorStore.search` returns no results (fresh install, nothing indexed), `retrieve()` returns `[]` immediately without calling SQLite.
- `retrieve_by_topic` never touches Bedrock or Chroma — it's a plain filtered SQLite lookup.

## What it replaced in issue #7's think flow

`AppService._run_turn` (`core/service.py`) previously classified intent and generated the reply in a single `BedrockService.structured()` call (no separate classification round-trip, per plan.md §9), then printed a static note — `"(long-term memory recall isn't wired up yet, so that answer only draws on this conversation.)"` — whenever the returned `turn.intent` was `question`, `recall`, or `resume`. No real memories were ever fetched or shown to the model.

That stub is gone. Because true intent isn't known until *after* the structured call returns, retrieval is now triggered *before* that call by a cheap local heuristic on the raw user text (`_looks_like_retrieval_turn`: a `?`, or keywords like "remember", "recall", "earlier", "before", "resume", "what did"). When it fires, `MemoryRetriever.retrieve(user_text, k=RETRIEVAL_K=5)` runs and its results are passed into `conversation_prompt(relevant_memories=[...])`, so the model's reply is actually grounded in real memories for question/recall/resume-shaped turns (plan.md §8.3 layer 4). `turn.intent` from the model's response remains the authoritative classification used elsewhere (e.g. capture-turn detection for background extraction) — the heuristic only gates whether it's worth paying for a vector search before asking.
