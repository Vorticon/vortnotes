"""VortNotes package.

The Flask application remains defined in :mod:`vortnotes.webapp` so existing
deployments that run ``gunicorn app:app`` keep working.

For maintainability and future testing, we also expose an application factory
so you can run:

    flask --app vortnotes:create_app run
"""

from __future__ import annotations

from .logging_config import configure_logging


def create_app():
    """Return the Flask app (factory-style entrypoint)."""
    configure_logging("vortnotes")
    from .webapp import app  # imported after logging so early logs are captured

    # Optional CLI helpers
    try:
        from .cli import register_cli

        register_cli(app)
    except Exception:
        # CLI helpers should never prevent the web app from starting.
        pass

    return app
