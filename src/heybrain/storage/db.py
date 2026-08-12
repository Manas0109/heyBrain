"""SQLite connection factory.

Plain sqlite3, no ORM. Opens (creating if needed) the database at
$HEYBRAIN_HOME/brain.db, applies storage/schema.sql idempotently, and
configures the pragmas correctness and concurrent access depend on.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from heybrain.core.config import get_settings

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection to brain.db, creating and schema-ing it if needed."""
    path = db_path if db_path is not None else get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_SCHEMA_PATH.read_text())
    return conn
