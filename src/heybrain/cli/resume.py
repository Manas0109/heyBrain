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

from heybrain.cli import render
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
    with render.spinner("Loading recent topics…"):
        topics = service.list_recent_topics()
    if not topics:
        render.echo('No topics yet. Try `brain think "..."` first.')
        return None

    render.echo("Recent topics:")
    return render.select_from_topics(topics)


def run(topic: str | None, voice: bool) -> None:
    service = AppService(output_fn=render.echo, spinner_fn=render.spinner)

    if topic is None:
        resolved = _pick_topic(service)
    else:
        with render.spinner("Loading recent topics…"):
            known_topics = [
                summary.topic for summary in service.list_recent_topics(limit=1000)
            ]
        resolved = resolve_topic(topic, known_topics)
        if resolved is None:
            render.error(f"No topic found matching {topic!r}.")

    if resolved is None:
        return

    try:
        conversation = service.resume(resolved, voice=voice)
    except KeyboardInterrupt:
        # AppService.resume hands off into the same think loop that already
        # saves and closes on Ctrl-C; this is a defensive backstop so a
        # traceback can never reach the terminal.
        render.echo("\nInterrupted -- conversation saved.")
        raise typer.Exit(code=0)

    render.saved_conversation(conversation)

    if not service.join_pending_extraction(timeout=0):
        with render.spinner("Saving…"):
            service.join_pending_extraction()
