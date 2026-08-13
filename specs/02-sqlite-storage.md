# Spec 02 — SQLite Storage Layer

**Issue:** #15 (originally filed as "#2 — SQLite storage layer")

## Purpose

Persists conversations, messages, memories, tasks, reminders, and usage records in
SQLite. SQLite is the source of truth (plan.md §7); Chroma is a rebuildable index
keyed by `memories.id`. No ORM — plain `sqlite3`, with repositories mapping rows
directly to the Pydantic models from `core.models`.

## Public API

### `heybrain.storage.db`
- `get_connection(db_path: Path | None = None) -> sqlite3.Connection` — opens (creating
  if needed) the database at `db_path`, defaulting to `get_settings().db_path`
  (`$HEYBRAIN_HOME/brain.db`). Applies `storage/schema.sql` on every call. Row factory
  is `sqlite3.Row`.

### `heybrain.storage.repositories`

- **`ConversationRepo(conn)`**
  - `create(conversation: Conversation) -> Conversation`
  - `get(conversation_id: str) -> Conversation | None`
  - `update(conversation: Conversation) -> Conversation`
  - `list_recent(limit: int = 10) -> list[Conversation]` — most recently updated first.

- **`MessageRepo(conn)`**
  - `create(message: Message) -> Message`
  - `get(message_id: str) -> Message | None`
  - `list_by_conversation(conversation_id: str) -> list[Message]` — oldest first.

- **`MemoryRepo(conn)`**
  - `create(memory: Memory) -> Memory`
  - `get(memory_id: str) -> Memory | None`
  - `update(memory: Memory) -> Memory`
  - `get_many(ids: list[str]) -> list[Memory]` — order-preserving batch fetch.
  - `list_by_topic(topic: str) -> list[Memory]` — newest first.

- **`TaskRepo(conn)`**
  - `create(task: Task) -> Task`
  - `get(task_id: str) -> Task | None`
  - `update(task: Task) -> Task`
  - `list_by_conversation(conversation_id: str) -> list[Task]` — oldest first.

- **`ReminderRepo(conn)`**
  - `create(reminder: Reminder) -> Reminder`
  - `get(reminder_id: str) -> Reminder | None`
  - `update(reminder: Reminder) -> Reminder`
  - `list_pending_due_before(before: datetime) -> list[Reminder]` — `status='pending'`
    and `scheduled_at < before`, soonest first.

- **`UsageRepo(conn)`**
  - `create(usage: UsageRecord) -> UsageRecord`
  - `get(usage_id: str) -> UsageRecord | None`
  - `list_by_request(request_id: str) -> list[UsageRecord]` — oldest first.

## Schema (`storage/schema.sql`)

| Table | Key columns | Indexes |
|---|---|---|
| `conversations` | `id` PK, `title`, `summary`, `topic`, `status`, `created_at`, `updated_at` | — |
| `messages` | `id` PK, `conversation_id` FK→conversations, `role`, `content`, `created_at` | `conversation_id` |
| `memories` | `id` PK, `conversation_id` FK→conversations, `memory_type`, `content`, `topic`, `importance`, `status`, `created_at`, `updated_at` | `topic`, `status` |
| `tasks` | `id` PK, `conversation_id` FK→conversations, `title`, `description`, `status`, `created_at`, `completed_at` | — |
| `reminders` | `id` PK, `task_id` FK→tasks, `scheduled_at`, `status`, `fired_at`, `created_at` | `(status, scheduled_at)` |
| `usage` | `id` PK, `request_id`, `operation`, `model_id`, `input_tokens`, `output_tokens`, `latency_ms`, `created_at` | — |

All `CREATE TABLE` / `CREATE INDEX` statements use `IF NOT EXISTS` and are safe to
re-run on every connection open.

## Constraints Other Agents Must Respect

- **No migrations.** Schema changes during the hackathon mean deleting `brain.db` and
  letting `get_connection` recreate it from scratch — there is no upgrade path.
- **`MemoryRepo.get_many(ids)` preserves caller-supplied ordering**, not SQLite's
  (unspecified) result order. Retrieval ranks before fetching, so callers depend on
  this. Ids with no matching row are silently skipped, not raised.
- **Foreign-key violations raise `sqlite3.IntegrityError`**, they do not silently pass
  (`PRAGMA foreign_keys=ON` is set on every connection).
- **Pragmas in effect on every connection:** `foreign_keys=ON`, `journal_mode=WAL`.
- Timestamps are stored as ISO 8601 strings (`datetime.isoformat()`, always tz-aware
  UTC) and parsed back via `datetime.fromisoformat`; enums are stored as their string
  value (`StrEnum` members serialize directly, no explicit `.value` conversion needed
  in most cases).
- Repositories `commit()` after every write — callers do not manage transactions
  themselves.
- All repos take an already-open `sqlite3.Connection` in their constructor; they do
  not open or close connections themselves.
