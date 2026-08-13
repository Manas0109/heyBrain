# Spec 01 — Contracts, Scaffold, and Configuration

**Issue:** #14 (originally filed as "#1 — Contracts, scaffold, and configuration")

## Purpose

Defines the shared foundation every other heyBrain workstream builds on: the package
layout, runtime configuration, domain models, Bedrock structured-output schemas, and
the error hierarchy. No network I/O and no AWS credentials are required to import any
of these modules.

## Public API

### `heybrain.core.config`
- `Settings` (pydantic-settings, reads `.env` / env vars) — fields: `aws_region: str`,
  `aws_profile: str | None`, `bedrock_model_id: str`, `bedrock_fast_model_id: str`,
  `bedrock_embedding_model_id: str`, `heybrain_home: Path` (default `~/.heybrain`),
  `whisper_model: str`.
- `Settings.db_path`, `.chroma_dir`, `.tmp_dir`, `.models_dir` — derived paths under `heybrain_home`.
- `Settings.ensure_home()` — creates `heybrain_home` and its subdirectories.
- `get_settings() -> Settings` — cached singleton; call this, not `Settings()` directly, to get an initialized home dir.

### `heybrain.core.models` (pure Pydantic, no I/O)
- Enums: `Role` (`user`/`assistant`), `ConversationStatus` (`open`/`closed`),
  `MemoryType` (`idea`/`goal`/`preference`/`fact`/`decision`/`plan`),
  `MemoryStatus` (`active`/`archived`/`superseded`), `TaskStatus` (`open`/`completed`),
  `ReminderStatus` (`pending`/`fired`/`missed`).
- `Conversation`, `Message`, `Memory`, `Task`, `Reminder`, `UsageRecord` — the persisted
  domain rows; each has an auto `id: str` (uuid4 hex) and tz-aware `created_at`.
- `RecallResult` — `brain recall` output: `answer: str`, `memories: list[Memory]`.
- `TopicSummary` — derived (not stored) `topic: str` + `last_touched_at: datetime`, used
  by `resume`'s topic picker.

### `heybrain.bedrock.schemas` (structured-output schemas, no I/O)
- `Intent` enum: `capture | question | recall | resume | reminder`.
- `MemoryCandidate` — `content`, `memory_type: MemoryTypeLiteral`, `importance: float` (0.0–1.0), `topic`.
- `TaskCandidate` — `title`, `description`.
- `ReminderCandidate` — `title`, `scheduled_at: str` (ISO 8601), `recurrence: str | None`.
- `ConversationTurn` — `intent: Intent`, `reply: str` (intent classification + reply in one call).
- `ConversationAnalysis` — `title`, `summary`, `topic`, `memory_candidates: list[MemoryCandidate]`, `tasks: list[TaskCandidate]`.
- `RecallSynthesis` — `answer`, `source_memory_ids: list[str]`.
- `TopicReconstruction` — `topic`, `summary`, `open_threads: list[str]`.
- All extend `_StrictModel` (`extra="forbid"`) — treat as closed objects when using them as structured-output schemas.

### `heybrain.core.errors`
- `HeyBrainError` — base class; the CLI catches this and prints a sentence, never a traceback.
- `BedrockError(message, *, recoverable: bool = False)` — Bedrock call/response failures.
- `TranscriptionError` — audio capture / speech-to-text failures.
- `StorageError` — SQLite / Chroma failures.

### `heybrain.core.service.AppService`
The only orchestration layer; the CLI never calls Bedrock, SQL, or storage directly.
`AppService()` wires up real dependencies (SQLite connection, Chroma vector store,
`BedrockService`) via `get_settings()`; all dependencies are injectable for tests.

Public methods:
- `think(text: str | None = None, *, voice: bool = False) -> Conversation` — capture + converse loop.
- `remember(text: str) -> Memory` — force a long-term memory (bypasses importance threshold, still dedups).
- `recall(query: str) -> RecallResult` — semantic search + LLM synthesis.
- `resume(topic: str | None = None, *, voice: bool = False) -> Conversation` — reconstruct a topic and continue conversing.
- `list_recent_topics(limit: int = 10) -> list[TopicSummary]`.
- `list_conversations() -> list[Conversation]`.
- `show_conversation(conversation_id: str) -> tuple[Conversation, list[Message]]`.
- `get_task(task_id: str) -> Task | None`.
- `list_reminders() -> list[Reminder]` — pending, soonest first.
- `tick_reminders(*, now=None, notify_fn=None) -> ReminderTickSummary` — fires/misses due reminders.
- `reindex() -> int` — rebuilds Chroma from SQLite (SQLite is authoritative).
- `reprocess(conversation_id: str) -> list[Memory]` — re-run extraction on a past conversation.
- `join_pending_extraction(timeout: float | None = None) -> bool` — block for background memory extraction.
- `doctor() -> dict[str, bool]` — **not yet implemented** (raises `NotImplementedError`).

`ReminderTickSummary` (dataclass, also in `core.service`): `fired: list[Reminder]`, `missed: list[Reminder]`.

## Repo Layout

```
heybrain/
├── src/heybrain/
│   ├── cli/            # Typer app + per-command modules, render.py for output
│   ├── core/           # config.py, models.py, errors.py, service.py (AppService)
│   ├── bedrock/        # client.py, prompts.py, schemas.py, usage.py
│   ├── memory/         # service.py, extractor.py, retriever.py, vectors.py
│   ├── transcription/  # whisper.py
│   ├── audio/          # record.py
│   ├── reminders/      # service.py, notify.py
│   └── storage/        # db.py, schema.sql, repositories.py
├── tests/
├── scripts/
├── pyproject.toml
├── Makefile
└── .env.example
```

## Constraints Other Agents Must Respect

- `core/models.py` and `bedrock/schemas.py` are pure data — **no I/O, no network, no AWS
  credentials required** to import or instantiate them.
- All timestamps are timezone-aware `datetime` (UTC).
- `Memory.importance` / `MemoryCandidate.importance` is `float` constrained `0.0–1.0`.
- Structured-output schemas must stay API-compatible: no recursive types, no numeric
  constraints beyond simple bounds, `extra="forbid"` (closed objects) on every schema.
- The CLI layer (`cli/`) must never call Bedrock, SQL, or prompt text directly — always
  go through `AppService`.
- Model IDs are configuration (`Settings`), never hardcoded in orchestration code.
