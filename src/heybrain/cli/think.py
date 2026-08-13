"""`brain think` — capture + converse.

Arg handling and top-level Ctrl-C handling only. All orchestration
(persistence, Bedrock calls, context assembly) lives in AppService.think;
this module never calls Bedrock, touches SQL, or holds prompt text.
"""

from __future__ import annotations

import typer

from heybrain.core.errors import HeyBrainError
from heybrain.core.service import AppService


def run(text: list[str] | None, voice: bool) -> None:
    message = " ".join(text) if text else None
    service = AppService()

    try:
        conversation = service.think(message, voice=voice)
    except KeyboardInterrupt:
        # AppService.think already saves and closes on Ctrl-C; this is a
        # defensive backstop so a traceback can never reach the terminal.
        typer.echo("\nInterrupted — conversation saved.")
        raise typer.Exit(code=0)
    except HeyBrainError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1)

    label = f" — {conversation.title}" if conversation.title else ""
    typer.echo(f"\nSaved conversation {conversation.id}{label}")
