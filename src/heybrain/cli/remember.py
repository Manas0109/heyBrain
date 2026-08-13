"""`brain remember` — force a long-term memory, no conversation.

Thin wrapper on AppService.remember (issue #9's write path); this module
only echoes confirmation back to the user.
"""

from __future__ import annotations

import typer

from heybrain.core.errors import HeyBrainError
from heybrain.core.service import AppService


def run(text: str) -> None:
    service = AppService()
    try:
        memory = service.remember(text)
    except HeyBrainError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1)
    typer.echo(f"Remembered ({memory.memory_type.value}): {memory.content}")
