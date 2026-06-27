"""Database-scoped Media library.

Media is stored per selected database and can be viewed without unlocking when
"read without password" is enabled (similar to Links). Editing requires the DB
to be unlocked if it has a password.
"""

from __future__ import annotations

import json
import mimetypes

from flask import redirect, render_template, request, url_for


def register_media_routes(app) -> None:
    # Late imports to avoid cycles.
    from ..webapp import (
        _attachment_ext_allowed,
        _current_db_name,
        _is_unlocked,
        _upload_filename_for_db,
        current_upload_dir,
        db_guest_can,
        ensure_db_initialized,
        get_attachment_max_bytes,
        get_db,
        get_db_password_info,
        iso_now,
        resolve_db_path,
        touch_db_last_access,
        unique_store_name,
    )

    def _require_read_access(next_url: str):
        name = _current_db_name()
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        touch_db_last_access(name)
        salt, phash = get_db_password_info(db_path)
        if salt and phash and not _is_unlocked(name) and not db_guest_can(name, "content", "read"):
            return redirect(url_for("settings_page", name=name, next=next_url))
        return None

    def _require_write_access(next_url: str):
        name = _current_db_name()
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        touch_db_last_access(name)
        salt, phash = get_db_password_info(db_path)
        if salt and phash and not _is_unlocked(name) and not db_guest_can(name, "content", "manage"):
            return redirect(url_for("settings_page", name=name, next=next_url))
        return None

    def _kind_from_mime(m: str) -> str:
        m = (m or "").lower()
        if m.startswith("image/"):
            return "image"
        if m.startswith("audio/"):
            return "audio"
        if m.startswith("video/"):
            return "video"
        return "file"

    def _list_media():
        db = get_db()
        rows = db.execute(
            "SELECT id, original_name, stored_name, mime, created_at, display_order "
            "FROM media ORDER BY display_order ASC, id ASC"
        ).fetchall()
        out = []
        for r in rows:
            r = dict(r)
            r["url"] = url_for("uploaded_file", filename=_upload_filename_for_db(r["stored_name"]))
            kind = _kind_from_mime(r.get("mime"))
            r["is_image"] = kind == "image"
            r["is_audio"] = kind == "audio"
            r["is_video"] = kind == "video"
            out.append(r)
        return out

    @app.route("/media")
    def media():
        gate = _require_read_access(url_for("media"))
        if gate:
            return gate

        name = _current_db_name()
        can_edit = _is_unlocked(name) or db_guest_can(name, "content", "manage")
        db_path = resolve_db_path(name)
        salt, phash = get_db_password_info(db_path)
        if not (salt and phash):
            can_edit = True

        return render_template("media.html", items=_list_media(), edit_mode=False, can_edit=can_edit, error="")

    @app.route("/media/edit", methods=["GET", "POST"])
    def media_edit():
        gate = _require_write_access(url_for("media_edit"))
        if gate:
            return gate

        db = get_db()
        error = ""
        if request.method == "POST":
            # Update order
            state_raw = (request.form.get("media_state") or "").strip()
            if state_raw:
                try:
                    state = json.loads(state_raw)
                    for item in state:
                        mid = int(item.get("id"))
                        order = int(item.get("order", 0))
                        db.execute("UPDATE media SET display_order=? WHERE id=?", (order, mid))
                except Exception:
                    pass

            # Add new uploads (append)
            files = request.files.getlist("media_files")
            now = iso_now()
            media_dir = current_upload_dir() / "media"
            media_dir.mkdir(parents=True, exist_ok=True)

            # next order = max + 1
            row = db.execute("SELECT COALESCE(MAX(display_order), -1) AS m FROM media").fetchone()
            next_order = int(row["m"]) + 1 if row and row["m"] is not None else 0

            for f in files:
                if not f or not getattr(f, "filename", ""):
                    continue
                original = f.filename
                if not _attachment_ext_allowed(original):
                    # Keep going, but set an error so the user knows something was skipped.
                    error = error or "Some files were skipped (type not allowed)."
                    continue

                stored_name = unique_store_name(media_dir, original)
                max_bytes = int(get_attachment_max_bytes())
                data = f.stream.read(max_bytes + 1)
                if len(data) > max_bytes:
                    error = "One or more files were too large."
                    continue
                (media_dir / stored_name).write_bytes(data)

                # Determine mime
                mime = (getattr(f, "mimetype", "") or "").strip()
                if not mime or mime == "application/octet-stream":
                    mime = mimetypes.guess_type(original)[0] or "application/octet-stream"

                db.execute(
                    "INSERT INTO media (original_name, stored_name, mime, created_at, display_order) VALUES (?,?,?,?,?)",
                    (original, f"media/{stored_name}", mime, now, next_order),
                )
                next_order += 1

            db.commit()

        return render_template("media.html", items=_list_media(), edit_mode=True, can_edit=True, error=error)

    @app.route("/media/delete/<int:media_id>", methods=["POST"])
    def media_delete(media_id: int):
        gate = _require_write_access(url_for("media_edit"))
        if gate:
            return gate

        db = get_db()
        row = db.execute("SELECT stored_name FROM media WHERE id=?", (media_id,)).fetchone()
        if row:
            stored = row["stored_name"]
            db.execute("DELETE FROM media WHERE id=?", (media_id,))
            db.commit()

            # Delete file from disk
            try:
                # stored is like "media/<file>" under the current DB upload dir
                (current_upload_dir() / stored).unlink(missing_ok=True)
            except Exception:
                pass

        return redirect(url_for("media_edit"))
