"""Error handlers.

Keeps user-facing error pages and HTTP error handling out of the main webapp
module so routes + helpers stay easier to navigate.
"""

from __future__ import annotations

from flask import render_template, request


def register_error_handlers(app) -> None:
    @app.errorhandler(413)
    def request_entity_too_large(_err):
        limit = app.config.get("MAX_CONTENT_LENGTH")
        max_mb = int(limit / (1024 * 1024)) if isinstance(limit, int) else None

        # Prefer JSON for XHR calls.
        if request.path.startswith("/api/") or request.headers.get("Accept", "").startswith("application/json"):
            return {"error": "The upload exceeded the maximum request size.", "max_mb": max_mb}, 413

        return render_template("413.html", max_mb=max_mb), 413

    @app.errorhandler(404)
    def page_not_found(_err):
        return render_template("404.html"), 404
