# Issue #8 — Chroma vector store wrapper

## Purpose

`heybrain.memory.vectors.VectorStore` persists and searches memory embeddings
in a local Chroma collection at `$HEYBRAIN_HOME/chroma/`. SQLite
(`heybrain.storage.repositories`) remains the source of truth for memory
content; Chroma is a disposable vector index that can always be rebuilt from
SQLite via `brain reindex`.

## Public API — `memory/vectors.py`

### `class VectorStore`

- `VectorStore(chroma_dir: Path | None = None)` — opens (creating if needed)
  a Chroma `PersistentClient` at `chroma_dir` (defaults to
  `get_settings().chroma_dir`) and gets/creates the single `"memories"`
  collection, configured for cosine distance (`hnsw.space = "cosine"`), so
  `search()`'s returned distance is `1 - cosine_similarity`.

- `upsert(memory_id: str, embedding: list[float], metadata: dict) -> None`
  Inserts or overwrites one vector by id.

- `search(embedding: list[float], k: int = 5, filters: dict | None = None) -> list[tuple[str, float]]`
  Returns up to `k` nearest `(memory_id, distance)` pairs, nearest first.
  Always filters `status = "active"` unless `filters` overrides `"status"`.
  Any keys in `filters` (e.g. `{"topic": "kafka"}`) are ANDed with the status
  filter.

- `delete(memory_id: str) -> None`
  Removes one vector by id.

- `rebuild(memories: list[Memory], embeddings: list[list[float]]) -> None`
  Drops and repopulates the whole collection from parallel `memories` /
  `embeddings` lists (same length, same order). This is what `brain reindex`
  calls after re-embedding every SQLite memory.

- `close() -> None`
  Releases the underlying Chroma client's file handles. Call before deleting
  or replacing `chroma_dir` out from under a live `VectorStore` (e.g. in
  tests) — otherwise the on-disk store is left open and a fresh client at the
  same path sees it as read-only.

### `memory_metadata(memory: Memory) -> dict`

Module-level helper that builds the metadata dict for a `Memory` (see schema
below). Used internally by `rebuild`; also useful for callers doing their own
`upsert`.

## Metadata schema (per vector)

| key               | type  | source                          |
|-------------------|-------|----------------------------------|
| `memory_type`     | str   | `Memory.memory_type.value`       |
| `topic`           | str   | `Memory.topic`                   |
| `importance`      | float | `Memory.importance`              |
| `status`          | str   | `Memory.status.value`            |
| `created_at`      | str   | `Memory.created_at.isoformat()`  |
| `conversation_id` | str   | `Memory.conversation_id`         |

## Key constraints

- **Caller always supplies embeddings.** `VectorStore` never calls Bedrock.
  Chroma's built-in embedding function is explicitly disabled at the
  collection level (`embedding_function=None` **and**
  `configuration={"embedding_function": None}` — passing only the former is
  not sufficient in chromadb ≥1.5 and silently falls back to a local
  MiniLM model). `add`/`query` without explicit vectors raises.
- **`search()` defaults to `status="active"`.** Pass `filters={"status": ...}`
  to override.
- **Chroma is disposable.** SQLite is authoritative; `brain reindex`
  (`AppService.reindex`, wired in `core/service.py`) re-embeds every SQLite
  memory via `BedrockService.embed()` and calls `VectorStore.rebuild()`.
