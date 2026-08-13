# Spec: Memory write path (issue #9)

## Purpose

Turns raw conversation content into a curated long-term memory store. Given a
conversation, it extracts durable, self-contained facts via an LLM, drops the
ones not worth keeping, and — before inserting anything new — checks whether
it's a near-duplicate of an existing memory, resolving that via an LLM
verdict rather than blindly appending. This is what keeps the memory store
from degenerating into `User is learning Kafka / User wants to learn Kafka /
...` (plan.md §8.2).

## Public API

- **`heybrain.memory.extractor.extract_candidates(bedrock: BedrockService, messages: list[Message]) -> list[MemoryCandidate]`**
  Runs `memory_extraction_prompt` over a message transcript via
  `BedrockService.structured`. Returns `[]` for an empty transcript or on any
  Bedrock failure — never raises.

- **`heybrain.memory.service.MemoryService`** — constructed with `bedrock`,
  `vector_store`, `memory_repo`, `message_repo`.
  - `process_conversation(conversation_id: str) -> list[Memory]` — the main
    entrypoint: extract → filter → write. Never raises.
  - `remember(text: str, conversation_id: str) -> Memory` — classify `text`
    like extraction would, force `importance=1.0`, then write. Can raise
    (embed/storage failures propagate; the caller is waiting synchronously).
  - `write_candidate(candidate: MemoryCandidate, conversation_id: str) -> Memory`
    — embed one candidate, dedupe against the nearest memory, persist. Can
    raise for the same reason as `remember`.

- **`AppService.remember(text: str) -> Memory`** — creates a closed
  container `Conversation` for the memory, then calls
  `MemoryService.remember`. Synchronous.

- **`AppService.reprocess(conversation_id: str) -> list[Memory]`** —
  re-runs `MemoryService.process_conversation` for an existing conversation.
  Raises `HeyBrainError` if the conversation doesn't exist.

- **CLI**: `brain remember <text>` (→ `cli/remember.py` →
  `AppService.remember`) and `brain reprocess <conversation_id>` (→
  `AppService.reprocess`), both in `cli/main.py`.

## Pipeline (`MemoryService.write_candidate`)

1. **Importance filter** (`process_conversation` only, before this
   function runs): candidates with `importance < IMPORTANCE_THRESHOLD`
   (0.6) are dropped. `remember()` bypasses this by forcing importance to
   1.0 before calling `write_candidate`.
2. **Embed**: `bedrock.embed([candidate.content])`.
3. **Nearest-neighbor search**: `vector_store.search(embedding, k=1)` →
   `(memory_id, distance)`. The Chroma collection is configured
   `hnsw:space=cosine`, so `similarity = 1.0 - distance`.
4. **Dedup gate**: if `similarity >= DEDUPE_SIMILARITY_THRESHOLD` (0.90)
   *and* the matched id resolves to a real SQLite row (an orphan vector
   with no row falls through to step 5), call `dedupe_verdict_prompt` via
   `bedrock.structured` for a `merge | supersede | separate` verdict.
   Otherwise skip straight to step 5.
5. **Apply verdict**, writing Chroma before SQLite on every branch:
   - **merge** — overwrite the existing row's `content` with the model's
     `merged_content` (falls back to the candidate's content if empty),
     `upsert` its vector under the *same* id, then `UPDATE` the SQLite row.
     One memory, one id, before and after.
   - **supersede** — insert the new candidate as a normal new memory
     (vector then row, new id), then mark the old row `status=superseded`
     and `DELETE` its vector from Chroma. Two SQLite rows now exist; only
     the new one is `active`.
   - **separate** — insert the candidate as a new memory (vector then
     row). Both memories remain `active`.

## Constraints another agent must respect

- **Dedup LLM failure degrades to `separate`**, never raises. If
  `bedrock.structured(..., schema=DedupeVerdict, ...)` throws
  `HeyBrainError`, `MemoryService._get_verdict` catches it, logs, and
  returns `DedupeVerdict(verdict="separate")` — the candidate is always
  written, never dropped by an LLM hiccup.
- **Extraction failure never fails the conversation.**
  `extract_candidates` swallows `HeyBrainError` and returns `[]`;
  `process_conversation` additionally wraps the whole extract+write loop
  in a broad `try/except` and logs — it must never raise into a caller
  that already has the conversation saved (issue #7's flow).
- **Write ordering is Chroma-then-SQLite, always** — insert, merge, and
  supersede all upsert/delete the vector before touching the SQLite row.
  A crash mid-write leaves a harmless orphan vector (cleaned up by
  `brain reindex`, issue #8), never an *active* SQLite row with no vector
  (which would be invisible to future dedup searches and is exactly how
  duplicates re-accumulate).
- **Named config constants** (in `memory/service.py`, not inlined):
  `IMPORTANCE_THRESHOLD = 0.6` and `DEDUPE_SIMILARITY_THRESHOLD = 0.90`.

## What this replaced in issue #7's think-flow stub

`AppService._close_conversation` previously had:
`# Memory candidates / tasks extraction belongs to issue #9; not applied here.`

`think()` now tracks whether any turn in the conversation had `capture`
intent. If so, once the conversation is closed it calls
`_start_background_extraction(conversation_id)`, which runs
`MemoryService.process_conversation` on a daemon thread (guarded by
`AppService`'s `threading.Lock`, since the shared SQLite connection is now
opened with `check_same_thread=False`). `think()` returns immediately —
the reply the user sees was never blocked on extraction.

The CLI (`cli/think.py`) calls `AppService.join_pending_extraction(timeout=0)`
right after `think()` returns; if extraction hasn't finished, it shows a
"Saving…" spinner and calls `join_pending_extraction()` again (blocking)
before the process exits, so extraction is never silently killed by process
exit. `brain reprocess <conversation_id>` is the manual recovery path if it
ever is.
