"""Loaders for the eval set fixtures in this directory.

Plain JSON, no schema dependency — kept loadable by tests without pulling
in the rest of the app.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_EVAL_DIR = Path(__file__).parent


def _load(name: str) -> list[dict[str, Any]]:
    with (_EVAL_DIR / name).open() as f:
        return json.load(f)


def load_capture_examples() -> list[dict[str, Any]]:
    return _load("capture_examples.json")


def load_recall_queries() -> list[dict[str, Any]]:
    return _load("recall_queries.json")


def load_reminder_phrasings() -> list[dict[str, Any]]:
    return _load("reminder_phrasings.json")
