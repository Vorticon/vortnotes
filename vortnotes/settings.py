"""Centralized configuration & paths.

This module keeps configuration (paths, size limits, env overrides) in one
place. It intentionally stays dependency-light so other modules can import it
without circular imports.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # project root


def _get_env_path(name: str, default: Path) -> Path:
    val = os.environ.get(name)
    return Path(val).expanduser().resolve() if val else default


def _legacy_app_mount_has_data(base_dir: Path) -> bool:
    """Detect old Docker/Unraid installs that bind-mounted data under /app."""
    db_dir = base_dir / "dbs"
    upload_dir = base_dir / "uploads"
    config_path = base_dir / "config" / "config.json"
    try:
        if db_dir.exists() and any(db_dir.glob("*.db")):
            return True
    except Exception:
        pass
    try:
        if upload_dir.exists() and any(upload_dir.iterdir()):
            return True
    except Exception:
        pass
    return config_path.exists()


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _unique_backup_path(path: Path, stamp: str) -> Path:
    candidate = path.with_name(f"{path.stem}.pre_migration_{stamp}{path.suffix}")
    i = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}.pre_migration_{stamp}_{i}{path.suffix}")
        i += 1
    return candidate


def _copy_file_preserving_existing(src: Path, dst: Path, stamp: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        try:
            if src.read_bytes() == dst.read_bytes():
                return
        except Exception:
            pass
        dst.rename(_unique_backup_path(dst, stamp))
    shutil.copy2(src, dst)


def _copy_tree_preserving_existing(src: Path, dst: Path, stamp: str) -> None:
    if not src.exists():
        return
    for p in src.rglob("*"):
        if p.is_file():
            _copy_file_preserving_existing(p, dst / p.relative_to(src), stamp)


def _migrate_legacy_app_data(base_dir: Path, data_dir: Path) -> bool:
    marker = data_dir / ".legacy_app_migration_complete"
    if marker.exists():
        return True

    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = os.environ.get("VORTNOTES_MIGRATION_STAMP", "").strip()
    if not stamp:
        from datetime import datetime

        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    for dirname in ("dbs", "uploads", "backups", "logs"):
        _copy_tree_preserving_existing(base_dir / dirname, data_dir / dirname, stamp)

    config_file = base_dir / "config" / "config.json"
    if config_file.exists():
        _copy_file_preserving_existing(config_file, data_dir / "config" / "config.json", stamp)

    secret_key = base_dir / ".secret_key"
    if secret_key.exists():
        _copy_file_preserving_existing(secret_key, data_dir / ".secret_key", stamp)

    marker.write_text(
        "Migrated legacy /app data into NOTES_DATA_DIR. Source data was left in place.\n",
        encoding="utf-8",
    )
    return True


def _resolve_data_dir() -> Path:
    raw = os.environ.get("NOTES_DATA_DIR")
    requested = _get_env_path("NOTES_DATA_DIR", BASE_DIR)
    # New Docker images use /data. Older Unraid templates mounted persistent
    # folders to /app/dbs, /app/uploads, and /app/config/config.json. If an
    # existing install updates in-place, prefer those legacy mounts instead of
    # creating a fresh empty DB in Docker's anonymous /data volume.
    if raw and raw.strip().replace("\\", "/") == "/data" and _legacy_app_mount_has_data(BASE_DIR):
        if _truthy_env("VORTNOTES_MIGRATE_LEGACY_APP_DATA"):
            try:
                if _migrate_legacy_app_data(BASE_DIR, requested):
                    return requested
            except Exception:
                pass
        return BASE_DIR
    return requested


# Persistent data dir (Docker/Unraid maps this)
DATA_DIR = _resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_DIR = DATA_DIR / "dbs"
DB_DIR.mkdir(exist_ok=True)

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

CONFIG_PATH = DATA_DIR / "config/config.json"
CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
SECRET_KEY_PATH = DATA_DIR / ".secret_key"

# Backward compatible: if notes.db exists in root, treat it as a candidate DB
LEGACY_DB = DATA_DIR / "notes.db"

# Logging
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


# Upload limits (defaults align with UI/expectations)
INLINE_IMAGE_MAX_MB = _env_int("VORTNOTES_INLINE_IMAGE_MAX_MB", 50)
INLINE_VIDEO_MAX_MB = _env_int("VORTNOTES_INLINE_VIDEO_MAX_MB", 50)

# Attachments / Media library uploads (per-file). Default is 50MB.
# Backward-compat: VORTNOTES_ATTACHMENT_MAX_GB is still supported.
ATTACHMENT_MAX_GB = _env_int("VORTNOTES_ATTACHMENT_MAX_GB", 5)
ATTACHMENT_MAX_MB = _env_int("VORTNOTES_ATTACHMENT_MAX_MB", 50)
try:
    _legacy_gb_raw = (os.environ.get("VORTNOTES_ATTACHMENT_MAX_GB") or "").strip()
    if _legacy_gb_raw:
        # If set, override MB via GB (legacy env var)
        ATTACHMENT_MAX_MB = max(0, int(_legacy_gb_raw)) * 1024
except Exception:
    pass

# Flask/Werkzeug max request size should cover inline uploads.
MAX_CONTENT_LENGTH_MB = _env_int("VORTNOTES_MAX_CONTENT_LENGTH_MB", 50)
