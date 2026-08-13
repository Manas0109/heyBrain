"""`brain recall` — semantic search + LLM synthesis.

All orchestration (retrieval, synthesis) lives in AppService.recall; this
module only renders the result: the synthesized answer prominently, with
numbered, attributed source memories beneath it (plan.md §5, §8.4).
"""

from __future__ import annotations

import typer
from rich.console import Console

from heybrain.core.errors import HeyBrainError
from heybrain.core.service import AppService


def run(query: str) -> None:
    service = AppService()
    try:
        result = service.recall(query)
    except HeyBrainError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1)

    console = Console()
    console.print(result.answer, style="bold")

    if not result.memories:
        return

    console.print("")
    for index, memory in enumerate(result.memories, start=1):
        when = memory.created_at.strftime("%Y-%m-%d")
        console.print(
            f"[{index}] [reverse] {memory.memory_type.value} [/reverse]  "
            f"{memory.topic}  ({when})"
        )
