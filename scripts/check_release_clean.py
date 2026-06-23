"""Verify that release tooling excludes private/runtime material."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_IGNORES = {
    ".env",
    ".env.*",
    ".secret_key",
    "*.db",
    "*.crt",
    "*.key",
    "*.pem",
    "*.zip",
    "backups/",
    "config/",
    "dbs/",
    "logs/",
    "uploads/",
}
REQUIRED_RELEASE_FILES = {
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "SECURITY.md",
    "VERSION",
    "static/vendor/quill/quill.js.LICENSE.txt",
}


def violations(root: Path = ROOT) -> list[str]:
    dockerignore = {
        line.strip()
        for line in (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    problems = [f".dockerignore is missing: {item}" for item in sorted(REQUIRED_IGNORES - dockerignore)]
    problems.extend(
        f"required release file is missing: {item}"
        for item in sorted(REQUIRED_RELEASE_FILES)
        if not (root / item).is_file()
    )
    return problems


if __name__ == "__main__":
    problems = violations()
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        raise SystemExit(1)
    print("Release exclusions and required legal/security files are present.")
