"""`brain remember` — force a long-term memory, no conversation.

Thin wrapper on AppService.remember (issue #9's write path); this module
only echoes confirmation back to the user.
"""

from __future__ import annotations

from heybrain.cli import render
from heybrain.core.service import AppService


def run(text: str) -> None:
    service = AppService(spinner_fn=render.spinner)
    memory = service.remember(text)
    render.print_remembered(memory)
