"""Command-line helpers for VortNotes.

These are optional conveniences for maintenance and debugging.

Usage:
    flask --app vortnotes:create_app routes
    flask --app vortnotes:create_app vn-health
"""

from __future__ import annotations

import json
import os
from typing import Any

import click
from flask import Flask


def register_cli(app: Flask) -> None:
    @app.cli.command("vn-health")
    def vn_health() -> None:
        """Print basic app health metadata."""
        info: dict[str, Any] = {
            "app": "vortnotes",
            "debug": bool(app.config.get("DEBUG")),
            "max_content_length": app.config.get("MAX_CONTENT_LENGTH"),
            "data_dir": os.getenv("NOTES_DATA_DIR", "(default)"),
        }
        click.echo(json.dumps(info, indent=2, sort_keys=True))
