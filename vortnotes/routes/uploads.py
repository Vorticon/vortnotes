"""Upload-related routes.

This module keeps upload endpoints out of the main webapp module to reduce its
size and make it easier to test/iterate.
"""

from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, request, send_from_directory
from werkzeug.utils import secure_filename


def register_upload_routes(
    app: Flask,
    *,
    upload_root_dir: Path,
    inline_media_max_bytes_fn,
    current_upload_dir,
    selected_db_name,
    upload_relpath_for_db,
    unique_store_name,
    save_with_size_limit,
) -> None:
    """Register upload-related routes on *app*.

    We inject dependencies from :mod:`vortnotes.webapp` to avoid circular imports.
    """

    @app.route("/api/inline-upload", methods=["POST"])
    def api_inline_upload():
        """Upload an inline image for the editor and return a URL."""

        f = request.files.get("file")
        if not f or not getattr(f, "filename", ""):
            return (
                json.dumps({"ok": False, "error": "No file uploaded"}),
                400,
                {"Content-Type": "application/json"},
            )

        # Only allow common raster image types (SVG is intentionally disallowed).
        fname = secure_filename(f.filename) or "image"
        ext = Path(fname).suffix.lower()
        allowed = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        if ext not in allowed:
            return (
                json.dumps({"ok": False, "error": "Unsupported image type"}),
                400,
                {"Content-Type": "application/json"},
            )

        target_dir = current_upload_dir()
        stored = unique_store_name(target_dir, fname)
        dest = target_dir / stored

        try:
            limit = int(inline_media_max_bytes_fn())
        except Exception:
            limit = 0
        ok, err = save_with_size_limit(f, dest, limit)
        if not ok:
            return (
                json.dumps({"ok": False, "error": err or "Upload failed"}),
                400,
                {"Content-Type": "application/json"},
            )

        rel = upload_relpath_for_db(stored, selected_db_name())
        url = f"/uploads/{rel}"
        return (json.dumps({"ok": True, "url": url}), 200, {"Content-Type": "application/json"})

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename: str):
        return send_from_directory(upload_root_dir, filename)
