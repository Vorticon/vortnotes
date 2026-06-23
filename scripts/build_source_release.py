"""Build a deterministic source ZIP without private or runtime data."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
OUTPUT = ROOT / "release" / f"vortnotes-{VERSION}-source.zip"
EXCLUDED_PARTS = {
    ".git",
    ".github",  # release consumers do not need CI internals
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "backups",
    "config",
    "dbs",
    "logs",
    "release",
    "uploads",
}
EXCLUDED_NAMES = {".env", ".secret_key", "notes.db"}
EXCLUDED_SUFFIXES = {".crt", ".db", ".key", ".log", ".pem", ".pyc", ".zip"}


def included_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if not path.is_file() or any(
            part in EXCLUDED_PARTS or part.startswith(".pytest-tmp") for part in relative.parts
        ):
            continue
        if path.name in EXCLUDED_NAMES or path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        files.append(path)
    return sorted(files)


def build() -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in included_files():
            relative = path.relative_to(ROOT)
            info = zipfile.ZipInfo.from_file(path, f"vortnotes-{VERSION}/{relative.as_posix()}")
            info.date_time = (2026, 6, 21, 0, 0, 0)
            info.external_attr = (0o755 if os.access(path, os.X_OK) else 0o644) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return OUTPUT


if __name__ == "__main__":
    print(build())
