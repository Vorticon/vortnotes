"""Ordered SQLite schema migrations.

The legacy bootstrap remains idempotent for old installations. This registry
provides an auditable version boundary for this and future releases.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]


def _baseline_v1(_db: sqlite3.Connection) -> None:
    """Record the schema shipped before formal migration tracking."""


def _sticky_notes_v2(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS sticky_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT 'yellow',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_sticky_notes_updated ON sticky_notes(updated_at DESC, id DESC)")


MIGRATIONS: tuple[Migration, ...] = (
    (1, "legacy_schema_baseline", _baseline_v1),
    (2, "sticky_notes", _sticky_notes_v2),
)
LATEST_SCHEMA_VERSION = MIGRATIONS[-1][0]


def _backup_before_upgrade(db: sqlite3.Connection, db_path: Path, backup_dir: Path, target: int) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"{db_path.stem}.pre_schema_v{target}.{stamp}.db"
    backup = sqlite3.connect(destination)
    try:
        db.backup(backup)
    finally:
        backup.close()
    return destination


def apply_migrations(
    db: sqlite3.Connection,
    *,
    db_path: Path | None = None,
    backup_dir: Path | None = None,
    backup_existing: bool = False,
) -> list[int]:
    """Apply pending migrations atomically and return their version numbers."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = {int(row[0]) for row in db.execute("SELECT version FROM schema_migrations")}
    pending = [migration for migration in MIGRATIONS if migration[0] not in applied]
    if pending and backup_existing and db_path is not None and backup_dir is not None:
        _backup_before_upgrade(db, db_path, backup_dir, pending[-1][0])
    completed: list[int] = []
    for version, name, migrate in pending:
        migrate(db)
        db.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, datetime.now(timezone.utc).isoformat()),
        )
        db.execute(f"PRAGMA user_version = {version}")
        completed.append(version)
    return completed
