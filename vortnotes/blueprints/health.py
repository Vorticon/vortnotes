"""Health & metadata endpoints.

These endpoints are intentionally lightweight and safe to expose behind auth or
internal networks. They do not leak secrets.
"""

from __future__ import annotations

import os
import platform
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify

bp = Blueprint("health", __name__)


def _version() -> str:
    # Prefer explicit version set by deploy pipeline.
    v = os.getenv("VORTNOTES_VERSION", "").strip()
    if v:
        return v
    # Fall back to the version file included in source and container releases.
    try:
        return (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return "dev"


@bp.get("/healthz")
@bp.get("/health")
def healthz():
    return jsonify(
        ok=True,
        app="vortnotes",
        version=_version(),
        time=int(time.time()),
    )


@bp.get("/about")
def about():
    # Do NOT include secrets. Only include non-sensitive metadata.
    cfg = current_app.config
    return jsonify(
        app="vortnotes",
        version=_version(),
        debug=bool(cfg.get("DEBUG")),
        python=platform.python_version(),
        platform=platform.platform(),
        max_content_length=cfg.get("MAX_CONTENT_LENGTH"),
    )
