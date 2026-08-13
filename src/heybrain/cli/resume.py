"""`brain resume` -- list recent topics, reconstruct, continue.

Topic *resolution* (numbered picker when no topic is supplied; fuzzy
string matching when one is) lives here. There is no topics table (plan.md
§7), so matching is against string labels, not a foreign key -- this
module is the floor for that: exact match, then substring, then a
difflib fallback for typos. AppService.resume does the actual
reconstruction once it has an exact topic label.
"""

from __future__ import annotations

import difflib

import typer
from rich.console import Console

from heybrain.core.errors import HeyBrainError
from heybrain.core.service import AppService

_FUZZY_CUTOFF = 0.4


def resolve_topic(query: str, topics: list[str]) -> str | None:
    """Match `query` against known topic labels, or None if nothing is close.

    Case-insensitive exact match first, then substring containment (so
    'kafka' matches 'Kafka learning plan'), then a difflib close-match
    fallback for typos.
    """
    if not topics:
        return None
    lowered = query.strip().lower()

    for topic in topics:
        if topic.lower() == lowered:
            return topic

    substring_matches = [topic for topic in topics if lowered in topic.lower()]
    if substring_matches:
        return substring_matches[0]

    close = difflib.get_close_matches(query, topics, n=1, cutoff=_FUZZY_CUTOFF)
    return close[0] if close else None


def _pick_topic(service: AppService) -> str | None:
    topics = service.list_recent_topics()
    if not topics:
        typer.echo('No topics yet. Try `brain think "..."` first.')
        return None

    typer.echo("Recent topics:")
    for index, summary in enumerate(topics, start=1):
        when = summary.last_touched_at.strftime("%Y-%m-%d %H:%M")
        typer.echo(f"  {index}. {summary.topic}  (last touched {when})")

    choice = typer.prompt("Pick a topic number")
    try:
        index = int(choice)
    except ValueError:
        typer.echo(f"Not a number: {choice!r}")
        return None
    if not (1 <= index <= len(topics)):
        typer.echo(f"Out of range: {index}")
        return None
    return topics[index - 1].topic


def run(topic: str | None, voice: bool) -> None:
    service = AppService()

    if topic is None:
        resolved = _pick_topic(service)
    else:
        known_topics = [summary.topic for summary in service.list_recent_topics(limit=1000)]
        resolved = resolve_topic(topic, known_topics)
        if resolved is None:
            typer.echo(f"No topic found matching {topic!r}.")

    if resolved is None:
        return

    try:
        conversation = service.resume(resolved, voice=voice)
    except KeyboardInterrupt:
        # AppService.resume hands off into the same think loop that already
        # saves and closes on Ctrl-C; this is a defensive backstop so a
        # traceback can never reach the terminal.
        typer.echo("\nInterrupted -- conversation saved.")
        raise typer.Exit(code=0)
    except HeyBrainError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1)

    label = f" — {conversation.title}" if conversation.title else ""
    typer.echo(f"\nSaved conversation {conversation.id}{label}")

    if not service.join_pending_extraction(timeout=0):
        console = Console()
        with console.status("saving…", spinner="dots"):
            service.join_pending_extraction()
