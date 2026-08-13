"""`brain recall` — semantic search + LLM synthesis.

All orchestration (retrieval, synthesis) lives in AppService.recall; this
module only renders the result: the synthesized answer prominently, with
numbered, attributed source memories beneath it (plan.md §5, §8.4).
"""

from __future__ import annotations

from heybrain.cli import render
from heybrain.core.service import AppService


def run(query: str) -> None:
    service = AppService(spinner_fn=render.spinner)
    result = service.recall(query)
    render.print_recall_result(result)
