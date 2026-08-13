"""Typer app: `brain`. Parses args, renders output, calls AppService.

No Bedrock calls, no SQL, no prompt text lives here.
"""

from __future__ import annotations

import typer

from heybrain.cli import recall as recall_cli
from heybrain.cli import remember as remember_cli
from heybrain.cli import resume as resume_cli
from heybrain.cli import think as think_cli
from heybrain.core.errors import HeyBrainError
from heybrain.core.service import AppService

app = typer.Typer(name="brain", help="A laptop-first personal thinking and memory assistant.")
reminders_app = typer.Typer(help="Manage reminders.")
app.add_typer(reminders_app, name="reminders")


def _not_implemented(command: str) -> None:
    typer.echo(f"'{command}' is not implemented yet.")


@app.command()
def think(
    text: list[str] = typer.Argument(None),
    voice: bool = typer.Option(False, "--voice", help="Speak instead of typing."),
) -> None:
    """Capture + converse. No args → prompt or record."""
    think_cli.run(text, voice)


@app.command()
def remember(text: str) -> None:
    """Force a long-term memory, no conversation."""
    remember_cli.run(text)


@app.command()
def recall(query: str) -> None:
    """Semantic search + LLM synthesis."""
    recall_cli.run(query)


@app.command()
def resume(
    topic: str = typer.Argument(None),
    voice: bool = typer.Option(False, "--voice", help="Speak instead of typing."),
) -> None:
    """List recent topics, reconstruct, continue."""
    resume_cli.run(topic, voice)


@app.command(name="list")
def list_conversations() -> None:
    """Recent conversations."""
    service = AppService()
    conversations = service.list_conversations()
    if not conversations:
        typer.echo("No conversations yet. Try `brain think \"...\"`.")
        return
    for conversation in conversations:
        title = conversation.title or "(untitled)"
        when = conversation.updated_at.strftime("%Y-%m-%d %H:%M")
        typer.echo(f"{conversation.id}  [{conversation.status.value:6}]  {when}  {title}")


@app.command()
def show(conversation_id: str) -> None:
    """One conversation in full."""
    service = AppService()
    try:
        conversation, messages = service.show_conversation(conversation_id)
    except HeyBrainError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)

    typer.echo(f"# {conversation.title or '(untitled)'}")
    if conversation.summary:
        typer.echo(conversation.summary)
    typer.echo(f"topic: {conversation.topic or '-'}   status: {conversation.status.value}")
    typer.echo("")
    for message in messages:
        speaker = "You" if message.role.value == "user" else "brain"
        when = message.created_at.strftime("%H:%M:%S")
        typer.echo(f"[{when}] {speaker}: {message.content}")


@reminders_app.command(name="list")
def reminders_list() -> None:
    """Pending reminders."""
    _not_implemented("reminders list")


@reminders_app.command(name="tick")
def reminders_tick() -> None:
    """Internal: fire due reminders (called by launchd)."""
    _not_implemented("reminders tick")


@app.command()
def doctor() -> None:
    """Verify AWS creds, Bedrock access, mic, models."""
    _not_implemented("doctor")


@app.command()
def reindex() -> None:
    """Rebuild Chroma from SQLite. Chroma is disposable; SQLite is authoritative."""
    count = AppService().reindex()
    typer.echo(f"Reindexed {count} memories into Chroma.")


@app.command()
def reprocess(conversation_id: str) -> None:
    """Re-run memory extraction on an existing conversation.

    Escape hatch for a background extraction thread interrupted by process
    exit (plan.md §9) -- the conversation is safe, but its memories may
    never have been extracted.
    """
    service = AppService()
    try:
        memories = service.reprocess(conversation_id)
    except HeyBrainError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1)
    if not memories:
        typer.echo("No memories worth keeping were found.")
        return
    for memory in memories:
        typer.echo(f"- ({memory.memory_type.value}) {memory.content}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
