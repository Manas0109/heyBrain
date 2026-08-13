"""Typer app: `brain`. Parses args, renders output, calls AppService.

No Bedrock calls, no SQL, no prompt text lives here.
"""

from __future__ import annotations

import json

import typer

from heybrain.cli import recall as recall_cli
from heybrain.cli import remember as remember_cli
from heybrain.cli import render
from heybrain.cli import resume as resume_cli
from heybrain.cli import think as think_cli
from heybrain.core.service import AppService

app = typer.Typer(name="brain", help="A laptop-first personal thinking and memory assistant.")
reminders_app = typer.Typer(help="Manage reminders.")
app.add_typer(reminders_app, name="reminders")


@app.command()
@render.guard
def think(
    text: list[str] = typer.Argument(None),
    voice: bool = typer.Option(False, "--voice", help="Speak instead of typing."),
) -> None:
    """Capture + converse. No args → prompt or record."""
    think_cli.run(text, voice)


@app.command()
@render.guard
def remember(text: str) -> None:
    """Force a long-term memory, no conversation."""
    remember_cli.run(text)


@app.command()
@render.guard
def recall(query: str) -> None:
    """Semantic search + LLM synthesis."""
    recall_cli.run(query)


@app.command()
@render.guard
def resume(
    topic: str = typer.Argument(None),
    voice: bool = typer.Option(False, "--voice", help="Speak instead of typing."),
) -> None:
    """List recent topics, reconstruct, continue."""
    resume_cli.run(topic, voice)


@app.command(name="list")
@render.guard
def list_conversations(
    json_output: bool = typer.Option(False, "--json", help="Print conversations as a JSON array."),
) -> None:
    """Recent conversations."""
    service = AppService(spinner_fn=render.spinner)
    conversations = service.list_conversations()
    if json_output:
        typer.echo(
            json.dumps(
                [
                    {
                        "id": c.id,
                        "title": c.title,
                        "summary": c.summary,
                        "topic": c.topic,
                        "status": c.status,
                        "created_at": c.created_at,
                        "updated_at": c.updated_at,
                    }
                    for c in conversations
                ],
                default=str,
            )
        )
        return
    if not conversations:
        render.echo('No conversations yet. Try `brain think "..."`.')
        return
    render.print_conversations(conversations)


@app.command()
@render.guard
def show(conversation_id: str) -> None:
    """One conversation in full."""
    service = AppService(spinner_fn=render.spinner)
    conversation, messages = service.show_conversation(conversation_id)
    render.print_transcript(conversation, messages)


@reminders_app.command(name="list")
@render.guard
def reminders_list() -> None:
    """Pending reminders."""
    service = AppService(spinner_fn=render.spinner)
    reminders = service.list_reminders()
    if not reminders:
        render.echo("No pending reminders.")
        return
    render.print_reminders(reminders, service.get_task)


@reminders_app.command(name="tick")
@render.guard
def reminders_tick() -> None:
    """Internal: fire due reminders (called by launchd)."""
    service = AppService(spinner_fn=render.spinner)
    summary = service.tick_reminders()
    render.print_tick_summary(summary.fired, summary.missed, service.get_task)


@app.command()
@render.guard
def doctor() -> None:
    """Verify AWS creds, Bedrock access, mic, models."""
    render.not_implemented("doctor")


@app.command()
@render.guard
def reindex() -> None:
    """Rebuild Chroma from SQLite. Chroma is disposable; SQLite is authoritative."""
    service = AppService(spinner_fn=render.spinner)
    count = service.reindex()
    render.echo(f"Reindexed {count} memories into Chroma.")


@app.command()
@render.guard
def reprocess(conversation_id: str) -> None:
    """Re-run memory extraction on an existing conversation.

    Escape hatch for a background extraction thread interrupted by process
    exit (plan.md §9) -- the conversation is safe, but its memories may
    never have been extracted.
    """
    service = AppService(spinner_fn=render.spinner)
    memories = service.reprocess(conversation_id)
    if not memories:
        render.echo("No memories worth keeping were found.")
        return
    render.print_memories(memories)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
