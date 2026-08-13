"""`brain think` — capture + converse.

Arg handling and top-level Ctrl-C handling only. All orchestration
(persistence, Bedrock calls, context assembly) lives in AppService.think;
this module never calls Bedrock, touches SQL, or holds prompt text.
"""

from __future__ import annotations

import typer

from heybrain.cli import render
from heybrain.core.service import AppService

# plan.md §9 -- background extraction should finish almost instantly; this
# is just enough headroom to avoid interrupting a slow structured call.
_EXTRACTION_JOIN_POLL_SECONDS = 0.2


def run(text: list[str] | None, voice: bool) -> None:
    message = " ".join(text) if text else None
    service = AppService(output_fn=render.echo, spinner_fn=render.spinner)

    try:
        conversation = service.think(message, voice=voice)
    except KeyboardInterrupt:
        # AppService.think already saves and closes on Ctrl-C; this is a
        # defensive backstop so a traceback can never reach the terminal.
        render.echo("\nInterrupted — conversation saved.")
        raise typer.Exit(code=0)

    render.saved_conversation(conversation)

    # Capture-intent turns extract memories on a background thread (issue #9,
    # plan.md §9); join it here so the process never exits mid-write.
    if not service.join_pending_extraction(timeout=0):
        with render.spinner("Saving…"):
            service.join_pending_extraction()
