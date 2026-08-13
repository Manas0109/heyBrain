"""Rich-based terminal rendering for the `brain` CLI (plan.md §13 Phase 6, issue #14).

Every Typer command prints through here instead of ad-hoc `print`/`typer.echo`
calls, so styling -- memory badges, spinners, transcript formatting, error
rendering -- lives in one place. Functions take an optional `out: Console`
so tests can capture output without a real terminal.
"""

from __future__ import annotations

import functools
import logging
import sys
from contextlib import contextmanager
from typing import Callable, Iterator, ParamSpec, TypeVar

import questionary
import typer
from rich.console import Console
from rich.text import Text

from heybrain.core.errors import BedrockError, HeyBrainError, StorageError, TranscriptionError
from heybrain.core.models import (
    Conversation,
    Memory,
    MemoryType,
    Message,
    RecallResult,
    Reminder,
    ReminderStatus,
    Task,
    TopicSummary,
)

logger = logging.getLogger(__name__)

# Unexpected exceptions are logged (with a full traceback) via
# `logger.exception` below for debugging, but Python's logging module falls
# back to printing ERROR+ records straight to stderr when nothing in a
# logger's hierarchy has a handler attached. Since nothing else in this app
# configures logging, that fallback would put a raw traceback on the same
# terminal the user is watching -- exactly what render_exception exists to
# prevent. A NullHandler on the package logger absorbs those records instead;
# a real handler (e.g. a future `--debug` flag writing to a file) can still
# be added later without code changes here.
logging.getLogger("heybrain").addHandler(logging.NullHandler())

console = Console(highlight=False)

# One color per MemoryType, foreground+background reverse-video pairs rather
# than subtle foreground-only distinctions, so badges stay legible over a
# screen share regardless of the terminal's color depth or theme (issue #14
# technical requirements).
_MEMORY_TYPE_STYLES: dict[MemoryType, str] = {
    MemoryType.IDEA: "bold black on cyan",
    MemoryType.GOAL: "bold black on green",
    MemoryType.PREFERENCE: "bold black on magenta",
    MemoryType.FACT: "bold white on blue",
    MemoryType.DECISION: "bold black on yellow",
    MemoryType.PLAN: "bold black on bright_white",
}


def memory_badge(memory_type: MemoryType) -> Text:
    """A colored, high-contrast badge for one memory type."""
    return Text(f" {memory_type.value} ", style=_MEMORY_TYPE_STYLES[memory_type])


def memory_card(memory: Memory, *, index: int | None = None) -> Text:
    """One memory rendered as a badge + topic/date line + rewritten content."""
    when = memory.created_at.strftime("%Y-%m-%d")
    line = Text()
    if index is not None:
        line.append(f"[{index}] ")
    line.append_text(memory_badge(memory.memory_type))
    line.append(f"  {memory.topic}  ({when})\n    ")
    line.append(memory.content, style="italic")
    return line


def print_memories(memories: list[Memory], *, out: Console | None = None) -> None:
    c = out or console
    for index, memory in enumerate(memories, start=1):
        c.print(memory_card(memory, index=index))


def print_recall_result(result: RecallResult, *, out: Console | None = None) -> None:
    """Synthesized answer prominent, numbered sources beneath (plan.md §8.4)."""
    c = out or console
    c.print(Text(result.answer, style="bold"))
    if not result.memories:
        return
    c.print("")
    c.print("Sources:", style="dim")
    print_memories(result.memories, out=c)


def print_remembered(memory: Memory, *, out: Console | None = None) -> None:
    c = out or console
    c.print(Text("Remembered ", style="green"), memory_card(memory))


def print_transcript(
    conversation: Conversation, messages: list[Message], *, out: Console | None = None
) -> None:
    """`brain show` -- full conversation transcript."""
    c = out or console
    c.print(f"[bold]{conversation.title or '(untitled)'}[/bold]")
    if conversation.summary:
        c.print(conversation.summary, style="italic")
    status_style = "green" if conversation.status.value == "open" else "dim"
    c.print(
        f"topic: {conversation.topic or '-'}   "
        f"status: [{status_style}]{conversation.status.value}[/{status_style}]"
    )
    c.print("")
    for message in messages:
        is_user = message.role.value == "user"
        speaker, style = ("You", "bold cyan") if is_user else ("brain", "bold green")
        when = message.created_at.strftime("%H:%M:%S")
        row = Text()
        row.append(f"[{when}] ", style="dim")
        row.append(f"{speaker}: ", style=style)
        row.append(message.content)
        c.print(row)


def print_conversations(conversations: list[Conversation], *, out: Console | None = None) -> None:
    """`brain list` -- recent conversations."""
    c = out or console
    for conversation in conversations:
        title = conversation.title or "(untitled)"
        when = conversation.updated_at.strftime("%Y-%m-%d %H:%M")
        status_style = "green" if conversation.status.value == "open" else "dim"
        c.print(
            f"{conversation.id}  "
            f"[[{status_style}]{conversation.status.value:6}[/{status_style}]]  "
            f"{when}  {title}"
        )


def print_topics(topics: list[TopicSummary], *, out: Console | None = None) -> None:
    """`brain resume` -- numbered topic picker."""
    c = out or console
    for index, summary in enumerate(topics, start=1):
        when = summary.last_touched_at.strftime("%Y-%m-%d %H:%M")
        c.print(f"  {index}. {summary.topic}  [dim](last touched {when})[/dim]")


def select_from_topics(
    topics: list[TopicSummary],
    *,
    out: Console | None = None,
    interactive: bool | None = None,
) -> str | None:
    """`brain resume` -- pick one topic, or None if cancelled/invalid.

    Arrow-key select (via `questionary`) when attached to a real terminal.
    `questionary`/`prompt_toolkit` needs real cursor control, which piped
    input (scripts/demo.sh, CI, `docker run` without `-it`) doesn't have --
    those fall back to the plain numbered prompt this replaced, unchanged.

    `interactive` overrides the TTY autodetection; tests pass it explicitly
    instead of monkeypatching `sys.stdin`/`sys.stdout`.
    """
    c = out or console
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()

    if interactive:
        choices = [
            questionary.Choice(
                title=f"{summary.topic}  (last touched "
                f"{summary.last_touched_at.strftime('%Y-%m-%d %H:%M')})",
                value=summary.topic,
            )
            for summary in topics
        ]
        try:
            selected = questionary.select("Pick a topic", choices=choices).ask()
        except KeyboardInterrupt:
            # Backstop: questionary is documented to swallow Ctrl-C and
            # return None itself, but this guarantees no raw traceback can
            # reach the terminal regardless of version behavior (plan.md §15).
            selected = None
        if selected is None:
            c.print("[dim]Cancelled.[/dim]")
        return selected

    print_topics(topics, out=c)
    choice = typer.prompt("Pick a topic number")
    try:
        index = int(choice)
    except ValueError:
        error(f"Not a number: {choice!r}", out=c)
        return None
    if not (1 <= index <= len(topics)):
        error(f"Out of range: {index}", out=c)
        return None
    return topics[index - 1].topic


_REMINDER_STATUS_STYLES: dict[ReminderStatus, str] = {
    ReminderStatus.PENDING: "bold black on yellow",
    ReminderStatus.FIRED: "bold black on green",
    ReminderStatus.MISSED: "bold white on red",
}


def reminder_badge(status: ReminderStatus) -> Text:
    return Text(f" {status.value} ", style=_REMINDER_STATUS_STYLES[status])


def print_reminders(
    reminders: list[Reminder],
    get_task: Callable[[str], Task | None],
    *,
    out: Console | None = None,
) -> None:
    """`brain reminders list` -- pending reminders, soonest first."""
    c = out or console
    for reminder in reminders:
        task = get_task(reminder.task_id)
        title = task.title if task else "(unknown)"
        when = reminder.scheduled_at.strftime("%Y-%m-%d %H:%M %z")
        row = Text()
        row.append_text(reminder_badge(reminder.status))
        row.append(f"  {when}  {title}")
        c.print(row)


def print_tick_summary(
    fired: list[Reminder],
    missed: list[Reminder],
    get_task: Callable[[str], Task | None],
    *,
    out: Console | None = None,
) -> None:
    """`brain reminders tick` -- what just fired or was marked missed."""
    c = out or console
    if not fired and not missed:
        c.print("[dim]No due reminders.[/dim]")
        return
    if fired:
        print_reminders(fired, get_task, out=c)
    if missed:
        print_reminders(missed, get_task, out=c)
    c.print(f"[dim]Fired {len(fired)}, missed {len(missed)}.[/dim]")


def echo(text: str, *, out: Console | None = None) -> None:
    """Plain, neutral-styled line -- used for assistant replies and status text."""
    (out or console).print(text)


def saved_conversation(conversation: Conversation, *, out: Console | None = None) -> None:
    label = f" — {conversation.title}" if conversation.title else ""
    (out or console).print(f"\n[green]✓[/green] Saved conversation {conversation.id}{label}")


def not_implemented(command: str, *, out: Console | None = None) -> None:
    (out or console).print(f"[dim]'{command}' is not implemented yet.[/dim]")


def error(message: str, remediation: str | None = None, *, out: Console | None = None) -> None:
    """One red line + remediation text -- never a raw traceback (plan.md §15)."""
    c = out or console
    c.print(f"[bold red]✗ {message}[/bold red]")
    if remediation:
        c.print(f"  {remediation}", style="dim")


# Maps a HeyBrainError subclass to user-facing remediation text. Checked in
# isinstance order below -- these are sibling leaves, not a deep hierarchy,
# so first match is also the only match.
_REMEDIATIONS: dict[type[HeyBrainError], str] = {
    BedrockError: (
        "Check your AWS credentials/region and that the configured Bedrock "
        "models are enabled in that region -- see README.md and .env.example."
    ),
    TranscriptionError: (
        "Check System Settings → Privacy & Security → Microphone, "
        "or run the command again without --voice."
    ),
    StorageError: "Check that ~/.heybrain is writable and brain.db isn't corrupted.",
}


def render_exception(exc: Exception, *, out: Console | None = None) -> None:
    """Translate any exception into one red line + remediation, never a traceback."""
    if isinstance(exc, HeyBrainError):
        remediation = next(
            (text for error_type, text in _REMEDIATIONS.items() if isinstance(exc, error_type)),
            None,
        )
        error(str(exc), remediation, out=out)
        return

    # Anything else is a bug, not a documented failure mode (plan.md §15) --
    # log it in full for debugging but never print a traceback to the user.
    logger.exception("Unexpected error")
    error(
        f"Something went wrong internally ({type(exc).__name__}: {exc}).",
        "This shouldn't happen -- please report it.",
        out=out,
    )


@contextmanager
def spinner(label: str, *, out: Console | None = None) -> Iterator[None]:
    """Wrap a blocking call over ~500ms with a labeled spinner (issue #14)."""
    c = out or console
    with c.status(label, spinner="dots"):
        yield


P = ParamSpec("P")
T = TypeVar("T")


def guard(fn: Callable[P, T]) -> Callable[P, T | None]:
    """Wrap a Typer command so no unhandled exception reaches the terminal as a traceback.

    `KeyboardInterrupt` is intentionally not caught here -- `think`/`resume`
    handle it inline because it needs conversation-specific messaging; letting
    it propagate elsewhere matches Typer/Click's normal Ctrl-C handling.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T | None:
        try:
            return fn(*args, **kwargs)
        except typer.Exit:
            raise
        except HeyBrainError as exc:
            render_exception(exc)
            raise typer.Exit(code=1)
        except Exception as exc:  # defensive backstop -- plan.md §15
            render_exception(exc)
            raise typer.Exit(code=1)

    return wrapper
