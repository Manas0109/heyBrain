# Issue #13 — Reminders

Turns a spoken/typed reminder request into a real macOS notification: the think loop detects reminder intent, resolves the natural-language time to an absolute timezone-aware datetime (confirmed back to the user before saving), persists it as a `Task` + `Reminder`, and a periodic `tick` command fires notifications for whatever's due — with graceful handling of overdue and long-missed reminders.

## Public API

**Reminder-intent detection (think flow, `core/service.py`)**
- Each turn's `ConversationTurn` classification already includes `Intent.REMINDER` (issue #7). When a turn classifies as `reminder`, `AppService._run_turn` calls `AppService._handle_reminder(conversation, user_text)`.
- `_handle_reminder` calls `bedrock.structured(..., schema=ReminderCandidate)` using `bedrock/prompts.py::reminder_extraction_prompt(message, current_datetime, timezone)`, passing the caller's current local datetime/timezone (`datetime.now().astimezone()`) so relative phrasing ("tomorrow at 7pm") resolves correctly.
- The resolved absolute datetime is echoed to the user ("Got it — I'll remind you at ... to ...") **before** anything is saved.
- On confirmation, a `Task` (title = candidate title) and a `Reminder` (task_id, scheduled_at) are persisted via `TaskRepo`/`ReminderRepo`.

**`reminders/notify.py`**
- `notify(title: str, message: str) -> None` — fires a macOS banner via `osascript -e 'display notification "..." with title "..."'`. Escapes quotes/backslashes; never raises (best-effort delivery, swallows `OSError`/`SubprocessError`). macOS only, no cross-platform fallback.

**`AppService.tick_reminders(*, now=None, notify_fn=None) -> ReminderTickSummary`** (backs `brain reminders tick`)
- Selects pending reminders with `scheduled_at <= now`, classifies each as fire-now / fire-overdue / missed (see constraints below), calls `notify_fn(title, message)` for fired ones, and updates each reminder's status. Returns `ReminderTickSummary(fired: list[Reminder], missed: list[Reminder])`.
- `notify_fn` defaults to `reminders/notify.py::notify`; injectable for tests.
- CLI: `brain reminders tick` — intended to be invoked every 60s by launchd; prints a one-line fired/missed count.

**`AppService.list_reminders() -> list[Reminder]`** (backs `brain reminders list`)
- Returns all pending reminders. CLI resolves each reminder's task title via `AppService.get_task(task_id)` and prints `id / scheduled_at / title`.

**`scripts/install_launchd.sh`**
- Installs a macOS LaunchAgent (`~/Library/LaunchAgents/com.heybrain.reminders.tick.plist`) that runs `brain reminders tick` every 60 seconds (`StartInterval=60`, `RunAtLoad=true`). Idempotent — re-running unloads and reinstalls. Logs to `~/.heybrain/logs/`.

## Key constraints

- **Past-time rejection**: if the resolved `scheduled_at` is in the past (or unparseable / missing tzinfo), the reminder is never saved. The user is told the time already passed and re-asked; a blank reply skips the reminder entirely. Never silently fires.
- **Overdue tiers on tick**: for a due reminder (`scheduled_at <= now`), let `overdue = now - scheduled_at`.
  - `overdue <= 1 minute` (one tick interval): fires normally, no prefix.
  - `1 minute < overdue < 24 hours`: fires with an `"(overdue) "` message prefix.
  - `overdue >= 24 hours`: **not** fired — marked `missed` instead (`ReminderStatus.MISSED`). `brain reminders list` only shows `pending` reminders, so missed ones are visible only via the DB row (`reminders.status = 'missed'`), not the CLI.
- **Recurrence**: `ReminderCandidate.recurrence` is parsed and stored as free text by the extraction prompt, but only `recurrence == "none"` (i.e. unset) is honored in this MVP — no recurring-reminder scheduling logic exists.
- **Storage**: all datetimes (`Reminder.scheduled_at`, `fired_at`, `created_at`) are stored as timezone-aware ISO 8601 strings; naive datetimes are rejected during extraction parsing.
