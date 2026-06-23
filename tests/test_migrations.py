import sqlite3
from pathlib import Path

from vortnotes.migrations import LATEST_SCHEMA_VERSION, apply_migrations


def test_migrations_are_ordered_and_idempotent():
    db = sqlite3.connect(":memory:")
    assert apply_migrations(db) == list(range(1, LATEST_SCHEMA_VERSION + 1))
    assert apply_migrations(db) == []
    assert db.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
    versions = [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == list(range(1, LATEST_SCHEMA_VERSION + 1))
    assert db.execute("SELECT name FROM sqlite_master WHERE name='sticky_notes'").fetchone()


def test_existing_database_is_backed_up_before_upgrade(tmp_path: Path):
    path = tmp_path / "existing.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE original(value TEXT)")
    db.execute("INSERT INTO original VALUES ('preserved')")
    db.commit()
    backup_dir = tmp_path / "backups"
    apply_migrations(db, db_path=path, backup_dir=backup_dir, backup_existing=True)
    db.commit()
    db.close()
    backups = list(backup_dir.glob("existing.pre_schema_v*.db"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    assert backup.execute("SELECT value FROM original").fetchone()[0] == "preserved"
    backup.close()
