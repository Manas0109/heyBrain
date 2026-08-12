"""Typer app: `brain`. Parses args, renders output, calls AppService.

No Bedrock calls, no SQL, no prompt text lives here.
"""

from __future__ import annotations

import typer

app = typer.Typer(name="brain", help="A laptop-first personal thinking and memory assistant.")
reminders_app = typer.Typer(help="Manage reminders.")
app.add_typer(reminders_app, name="reminders")


def _not_implemented(command: str) -> None:
    typer.echo(f"'{command}' is not implemented yet.")


@app.command()
def think(text: list[str] = typer.Argument(None)) -> None:
    """Capture + converse. No args → prompt or record."""
    _not_implemented("think")


@app.command()
def remember(text: str) -> None:
    """Force a long-term memory, no conversation."""
    _not_implemented("remember")


@app.command()
def recall(query: str) -> None:
    """Semantic search + LLM synthesis."""
    _not_implemented("recall")


@app.command()
def resume(topic: str = typer.Argument(None)) -> None:
    """List recent topics, reconstruct, continue."""
    _not_implemented("resume")


@app.command(name="list")
def list_conversations() -> None:
    """Recent conversations."""
    _not_implemented("list")


@app.command()
def show(conversation_id: str) -> None:
    """One conversation in full."""
    _not_implemented("show")


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
