# Spec 04 — Prompt Library and Eval Set

**Issue:** #4 (github.com/Manas0109/heyBrain/issues/17)

## Purpose

`bedrock/prompts.py` holds every prompt string used by heyBrain's Bedrock calls, kept
separate from orchestration so prompt text is version-controlled and reusable across
callers. Each function is pure — no I/O, no Bedrock/langchain imports — and returns a
string (or, for `conversation_prompt`, a system prompt built from optional context).
`tests/eval/` is the small, hand-curated eval set (plan.md §16) used to tune these
prompts and to prove retrieval/extraction quality during development.

## Public API — `heybrain.bedrock.prompts`

- `conversation_prompt(*, conversation_summary: str | None = None, relevant_memories: list[str] | None = None) -> str`
  System prompt for one `think` turn. Pairs with `bedrock.schemas.ConversationTurn`
  (`intent: Intent`, `reply: str`) — intent classification and the reply are produced
  in the **same** structured-output call, on the primary model at `medium` effort
  (plan.md §9; no separate fast-model classification round-trip on this path).
- `memory_extraction_prompt(*, conversation_text: str) -> str`
  Pairs with `bedrock.schemas.ConversationAnalysis.memory_candidates` (`list[MemoryCandidate]`).
  Instructs rewriting each candidate as a self-contained third-person fact — never a
  quote of what was said — plus a short `topic` label and an `importance` score.
- `summarization_prompt(*, conversation_text: str) -> str`
  Pairs with `bedrock.schemas.ConversationAnalysis` (`title`, `summary`, `topic` fields).
- `recall_synthesis_prompt(*, query: str, memories: list[str]) -> str`
  Pairs with `bedrock.schemas.RecallSynthesis` (`answer`, `source_memory_ids`).
  Instructs answering only from the supplied memories; an empty `memories` list
  produces a prompt that pushes the model toward an honest "nothing found" answer.
- `continuation_prompt(*, topic: str, summaries: list[str], memories: list[str], open_tasks: list[str] | None = None) -> str`
  Pairs with `bedrock.schemas.TopicReconstruction` (`topic`, `summary`, `open_threads`).
  Instructs stating only what was previously discussed, ending with a forward-looking
  question, for `brain resume`.
- `reminder_extraction_prompt(*, message: str, current_datetime: str, timezone: str) -> str`
  Pairs with `bedrock.schemas.ReminderCandidate` (`title`, `scheduled_at`, `recurrence`).
  Requires the caller's current local datetime + timezone as context so relative
  phrasing ("tomorrow at 7pm") resolves to an absolute, timezone-aware ISO 8601 value.
- `dedupe_verdict_prompt(*, existing_memory: str, candidate_memory: str) -> str`
  Used in the memory write path's dedup step (plan.md §8.2) to get a
  `merge | supersede | separate` verdict when a candidate is a near-duplicate
  (cosine similarity ≥ 0.90) of an existing memory. No dedicated schema yet — callers
  currently parse/constrain the verdict themselves.
- `INTENTS` — module constant, `"capture, question, recall, resume, reminder"`, used
  inside `conversation_prompt`; not generally needed by callers.

## Eval Set — `tests/eval/`

Plain JSON fixtures, loaded via `tests/eval/loader.py` (`load_capture_examples()`,
`load_recall_queries()`, `load_reminder_phrasings()` — each returns
`list[dict[str, Any]]`, no schema dependency).

- **`capture_examples.json`** (10 items) — `{id, input, expected_intent, expected_memory, expected_memory_type}`.
  `expected_memory` is `null` for inputs that shouldn't produce a memory (low-importance
  chatter). Two entries (`cap-01`, `cap-02`) intentionally phrase the same fact two ways
  to exercise dedup.
- **`recall_queries.json`** (10 items) — `{id, query, expected_memory_content, notes}`.
  Queries deliberately avoid reusing the source wording (plan.md §8.4's semantic-search
  requirement); `expected_memory_content` matches an entry from `capture_examples.json`,
  or is `null` for a query that should return no confident match. `rec-07`/`rec-08` and
  `rec-02`/`rec-09` intentionally target the same underlying memory from different
  angles.
- **`reminder_phrasings.json`** (5 items) — `{id, phrasing, reference_datetime,
  timezone, expected_resolved_datetime, expected_title}`. `reference_datetime` is the
  fixed "now" to resolve relative phrasing against, so tests are deterministic; one
  entry (`rem-04`) specifically checks that a time already past today rolls to tomorrow.

To extend: append new objects to the relevant JSON file following the existing shape;
`tests/eval/test_eval_set.py` checks fixture counts/shape and that every non-null
`expected_memory` in `capture_examples.json` is covered by at least one
`recall_queries.json` entry, so keep both files in sync when adding capture examples.
Other agents building extraction/retrieval (issues #9, #10, #13) can run their
pipeline against these fixtures directly — the JSON has no dependency on
`bedrock/prompts.py` or any live Bedrock call.
