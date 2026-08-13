# Issue #14 — Terminal presentation and demo script

## Purpose

`cli/render.py` is the single place that turns domain models and exceptions
into terminal output for the `brain` CLI — colored memory/reminder badges,
spinners on every blocking call, a synthesized-answer-first `recall` layout,
transcript formatting, and error rendering that never leaks a traceback. All
CLI commands print through it instead of ad-hoc `print`/`typer.echo` calls,
and `scripts/demo.sh` exercises the full plan.md §1 demo path against real
Bedrock as a one-command, pre-demo smoke test.

## Public API — `cli/render.py`

All functions take an optional `out: Console` (default: the module-level
`console`), so callers/tests can capture output without a real terminal.

| Function | Call it when |
|---|---|
| `memory_badge(memory_type) -> Text` | You need just the colored type badge, e.g. inside a custom line. |
| `memory_card(memory, *, index=None) -> Text` | Rendering one memory (badge + topic + date + content). |
| `print_memories(memories)` | Rendering a numbered list of memories (`brain reprocess` output). |
| `print_recall_result(result: RecallResult)` | `brain recall` — synthesized answer first, numbered sources beneath. |
| `print_remembered(memory)` | `brain remember` confirmation. |
| `print_transcript(conversation, messages)` | `brain show` — full conversation transcript, speaker-labeled. |
| `print_conversations(conversations)` | `brain list`. |
| `print_topics(topics)` | `brain resume`'s numbered topic picker. |
| `reminder_badge(status) -> Text` | Colored badge for one `ReminderStatus`. |
| `print_reminders(reminders, get_task)` | `brain reminders list` — needs a `task_id -> Task \| None` lookup. |
| `print_tick_summary(fired, missed, get_task)` | `brain reminders tick` output. |
| `echo(text)` | Plain neutral-styled line (assistant replies, status text) — the default `output_fn` passed to `AppService`. |
| `saved_conversation(conversation)` | The "✓ Saved conversation …" line after `think`/`resume`. |
| `not_implemented(command)` | Stub commands not yet built. |
| `error(message, remediation=None)` | One red line + optional dim remediation line. |
| `render_exception(exc)` | Translates any exception into `error(...)` — see below. |
| `spinner(label)` | Context manager; wrap any call over ~500ms. Passed to `AppService(spinner_fn=render.spinner)`. |
| `guard(fn)` | Decorator for every Typer command — see below. |

## Guaranteed error rendering (no tracebacks)

Two layers make "no raw traceback reachable from a documented user action"
structural rather than a convention to remember:

1. **`render.guard`** wraps every `@app.command()` in `cli/main.py`. It lets
   `typer.Exit` pass through, catches `HeyBrainError` and translates it via
   `render_exception` (which maps `BedrockError`/`TranscriptionError`/
   `StorageError` to specific remediation text via a lookup table, falling
   back to just the message for other `HeyBrainError`s), and catches any
   other `Exception` as a defensive backstop — rendered as a generic
   "Something went wrong internally" line — before raising `typer.Exit(1)`.
   `KeyboardInterrupt` is deliberately *not* caught here; `think`/`resume`
   handle Ctrl-C inline so they can print conversation-specific messaging.
2. **Logging fallback suppression.** `render_exception` calls
   `logger.exception(...)` for unexpected errors so the full traceback is
   still captured for debugging. Python's `logging` module prints ERROR+
   records straight to stderr (`logging.lastResort`) when nothing in a
   logger's hierarchy has a handler — which would put a raw traceback back
   on the user's screen even with `guard` in place. `render.py` adds a
   `logging.NullHandler()` to the `"heybrain"` logger at import time to
   absorb those records instead. A real handler (e.g. a future `--debug`
   flag writing to a file) can be added later without touching this file.

Blocking Bedrock/transcription calls also can't hang silently: `AppService`
takes a `spinner_fn: Callable[[str], ContextManager[None]]` (default: a
no-op `nullcontext`), and every CLI entry point constructs `AppService` with
`spinner_fn=render.spinner`, so `core/service.py` never imports `rich`.

## Running `scripts/demo.sh`

```bash
scripts/demo.sh
```

Needs live AWS credentials and Bedrock model access (see README.md) — it is
a manual, pre-demo verification script, not part of the automated test
suite (same role as `scripts/bedrock_smoke.py`). It exercises, against real
Bedrock, in order:

1. Three `brain think` captures on different topics (each closed by piping
   `"exit"` so extraction runs, matching a real interactive session).
2. `brain recall` with a query worded differently than the captures.
3. `brain resume` with no topic argument (picks the most recent via the
   numbered picker), continuing the reconstructed conversation.
4. `brain list` and `brain show <id>` from fresh processes, proving
   persistence — `brain` is CLI-only with no daemon, so every step above is
   already a separate process/restart.
