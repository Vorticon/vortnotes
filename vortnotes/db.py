"""SQLite connection helpers.

Centralizes SQLite connection configuration (row factory, timeouts, FK pragma)
so the rest of the app doesn't repeat the same boilerplate.

This is a maintainability-focused module: behavior should remain identical.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union


def connect(db_path: Union[Path, str]) -> sqlite3.Connection:
    """Open a SQLite connection with app defaults."""
    conn = sqlite3.connect(str(Path(db_path)), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA busy_timeout = 5000")  # 5s
    except Exception:
        pass
    return conn


@contextmanager
def connection(db_path: Union[Path, str]) -> Iterator[sqlite3.Connection]:
    """Context manager that opens and closes a connection."""
    conn = connect(db_path)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass
