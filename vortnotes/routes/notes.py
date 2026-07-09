"""Note CRUD routes.

This module exists to keep :mod:`vortnotes.webapp` smaller and easier to
maintain. To avoid circular imports, we import :mod:`vortnotes.webapp` inside
the register function (after the app and helpers exist).
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from flask import abort, make_response, redirect, render_template, request, url_for

from ..sanitizer import sanitize_note_html


def register_note_routes(app) -> None:
    # Late imports (register is called after vortnotes.webapp is fully initialized),
    # so this does not create an import cycle.
    from ..webapp import (
        _attachment_ext_allowed,
        _current_db_name,
        _is_unlocked,
        _notes_where_clause,
        _parse_index_filters,
        _save_with_size_limit,
        _table_exists,
        _table_has_column,
        _title_select_expr,
        _upload_filename_for_db,
        current_upload_dir,
        db_guest_can,
        ensure_db_initialized,
        get_attachment_max_bytes,
        get_db,
        get_db_password_info,
        is_image_filename,
        iso_now,
        list_db_files,
        next_attachment_order,
        normalize_tags,
        resolve_db_path,
        touch_db_last_access,
        unique_store_name,
    )

    def _attachment_mime(row: dict) -> str:
        name = (row.get("original_name") or row.get("stored_name") or "").strip()
        guess, _ = mimetypes.guess_type(name)
        return (guess or "").lower()

    def _decorate_attachment(row) -> dict:
        a = dict(row)
        mime = _attachment_mime(a)
        a["mime"] = mime
        a["is_image"] = bool(mime.startswith("image/")) or is_image_filename(a["original_name"])
        a["is_video"] = bool(mime.startswith("video/"))
        a["is_audio"] = bool(mime.startswith("audio/"))
        return a

    def _save_attachment_icon_upload(file_storage) -> str:
        if not file_storage or not getattr(file_storage, "filename", ""):
            return ""
        if not _attachment_ext_allowed(file_storage.filename):
            return ""
        icon_dir = current_upload_dir() / "attachment_icons"
        icon_dir.mkdir(parents=True, exist_ok=True)
        max_bytes = int(get_attachment_max_bytes())
        data = file_storage.stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            return ""

        try:
            from io import BytesIO

            from PIL import Image

            icon_px = 256
            im = Image.open(BytesIO(data))
            im.load()
            if im.mode not in ("RGBA", "LA"):
                im = im.convert("RGBA")
            im.thumbnail((icon_px, icon_px), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (icon_px, icon_px), (0, 0, 0, 0))
            canvas.paste(im, ((icon_px - im.width) // 2, (icon_px - im.height) // 2), im)
            out = BytesIO()
            canvas.save(out, format="PNG", optimize=True)
            data = out.getvalue()
            stored_name = unique_store_name(icon_dir, "icon.png")
        except Exception:
            stored_name = unique_store_name(icon_dir, file_storage.filename)

        (icon_dir / stored_name).write_bytes(data)
        return f"attachment_icons/{stored_name}"

    @app.route("/")
    def index():
        name = _current_db_name()
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        touch_db_last_access(name)
        salt, phash = get_db_password_info(db_path)
        if salt and phash and not _is_unlocked(name) and not db_guest_can(name, "notes", "read"):
            return redirect(url_for("settings_page", name=name, next=url_for("index")))

        # If the DB is password-protected and currently not unlocked, user is in read-only mode.
        can_edit = not (salt and phash and not _is_unlocked(name)) or db_guest_can(name, "notes", "write")

        db = get_db()

        # Server-side filtering + pagination + sorting.
        filters = _parse_index_filters(request.args)
        try:
            page = max(1, int(request.args.get("page", "1")))
        except Exception:
            page = 1
        per_page = 20
        offset = (page - 1) * per_page

        # Sorting
        sort = (request.args.get("sort") or "date").strip().lower()
        direction = (request.args.get("dir") or "desc").strip().lower()
        if direction not in {"asc", "desc"}:
            direction = "desc"
        if sort not in {"id", "title", "tag", "date"}:
            sort = "date"

        where_sql, params = _notes_where_clause(db, filters)
        total = db.execute(f"SELECT COUNT(1) AS c FROM notes {where_sql}", params).fetchone()["c"]

        total_pages = max(1, (int(total) + per_page - 1) // per_page)
        if page > total_pages:
            page = total_pages
            offset = (page - 1) * per_page

        title_expr = _title_select_expr(db)
        has_pinned = _table_has_column(db, "notes", "pinned")

        if sort == "title":
            base_order = f"{title_expr} {direction}, id DESC"
        elif sort == "tag":
            base_order = f"tag {direction}, id DESC"
        elif sort == "date":
            base_order = f"created_at {direction}, id DESC"
        else:
            base_order = f"id {direction}"

        # Always show pinned notes first (if supported by the DB schema).
        order_sql = f"ORDER BY {'pinned DESC, ' if has_pinned else ''}{base_order}"

        select_fields = f"id, {title_expr} AS title, tag, created_at"
        if has_pinned:
            select_fields += ", pinned"

        # Attachment count (fast per-row correlated subquery; only enabled when attachments table exists).
        has_attachments = _table_exists(db, "attachments") and _table_has_column(db, "attachments", "note_id")
        if has_attachments:
            select_fields += ", (SELECT COUNT(1) FROM attachments a WHERE a.note_id = notes.id) AS attach_count"

        notes = db.execute(
            f"SELECT {select_fields} FROM notes {where_sql} {order_sql} LIMIT ? OFFSET ?",
            (*params, per_page, offset),
        ).fetchall()

        return render_template(
            "index.html",
            notes=notes,
            dbs=list_db_files(),
            selected_db=name,
            can_edit=can_edit,
            filters=filters,
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
            sort=sort,
            direction=direction,
        )

    @app.route("/select-db", methods=["POST"])
    def select_db_from_main():
        # Select a DB from the main page (no admin password required).
        name = (request.form.get("name") or "").strip()
        if not name:
            return redirect(url_for("index"))
        if not name.endswith(".db"):
            name += ".db"

        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)

        salt, phash = get_db_password_info(db_path)
        if salt and phash and not _is_unlocked(name) and not db_guest_can(name, "notes", "read"):
            resp = make_response(redirect(url_for("settings_page", name=name, next=url_for("index"))))
        else:
            resp = make_response(redirect(url_for("index")))

        resp.set_cookie("selected_db", name, max_age=60 * 60 * 24 * 365, samesite="Lax")
        return resp

    @app.route("/notes/new", methods=["GET", "POST"])
    def new_note():
        # Read-only protection (password-protected DB that isn't unlocked)
        name = _current_db_name()
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        salt, phash = get_db_password_info(db_path)
        if salt and phash and not _is_unlocked(name) and not db_guest_can(name, "notes", "write"):
            return redirect(url_for("index"))

        if request.method == "GET":
            return render_template(
                "note_form.html",
                is_edit=False,
                note=dict(id=None, title="", description="", tag="", content_html=""),
                attachments=[],
            )

        title = (request.form.get("title") or "").strip() or "(Untitled)"
        tag = normalize_tags((request.form.get("tag") or ""))
        content_html = request.form.get("content_html") or ""
        content_html = sanitize_note_html(content_html)
        content_delta = (request.form.get("content_delta") or "").strip()
        # Store delta only if it's valid JSON; otherwise keep empty.
        if content_delta:
            try:
                json.loads(content_delta)
            except Exception:
                content_delta = ""
        now = iso_now()

        db = get_db()
        # Backward compatible insert across schema versions (older DBs may still have 'description' column).
        has_desc = _table_has_column(db, "notes", "description")
        has_title = _table_has_column(db, "notes", "title")
        has_updated = _table_has_column(db, "notes", "updated_at")

        cols = []
        vals = []
        if has_title:
            cols.append("title")
            vals.append(title)
        if has_desc:
            cols.append("description")
            vals.append(title)
        cols.append("tag")
        vals.append(tag)
        cols.append("created_at")
        vals.append(now)
        if has_updated:
            cols.append("updated_at")
            vals.append(now)
        cols.append("content_html")
        vals.append(content_html)
        if _table_has_column(db, "notes", "content_delta"):
            cols.append("content_delta")
            vals.append(content_delta)

        sql = f"INSERT INTO notes ({', '.join(cols)}) VALUES ({', '.join(['?']*len(cols))})"
        cur = db.execute(sql, tuple(vals))
        db.commit()
        note_id = cur.lastrowid

        files = request.files.getlist("attachments")
        target_dir = current_upload_dir()  # avoid recomputing per-file
        for f in files:
            if f and f.filename:
                original = f.filename
                stored = unique_store_name(target_dir, original)
                ext = (Path(original).suffix or "").lower()
                if not _attachment_ext_allowed(original):
                    return redirect(url_for("new_note", error=f"Blocked attachment type: {ext}"))
                max_bytes = int(get_attachment_max_bytes())
                ok, err = _save_with_size_limit(f, target_dir / stored, max_bytes)
                if not ok:
                    return redirect(
                        url_for("new_note", error=f"Attachment too large. Max is {max_bytes // (1024*1024*1024)}GB.")
                    )
                db.execute(
                    "INSERT INTO attachments (note_id, original_name, stored_name, created_at, display_order) VALUES (?,?,?,?,?)",
                    (note_id, original, stored, now, next_attachment_order(db, note_id)),
                )
        db.commit()
        return redirect(url_for("view_note", note_id=note_id))

    @app.route("/notes/<int:note_id>")
    def view_note(note_id):
        name = _current_db_name()
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        salt, phash = get_db_password_info(db_path)
        can_edit = not (salt and phash and not _is_unlocked(name)) or db_guest_can(name, "notes", "write")

        db = get_db()
        note = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if note is None:
            abort(404)

        attachments = db.execute(
            "SELECT * FROM attachments WHERE note_id=? ORDER BY display_order ASC, id ASC", (note_id,)
        ).fetchall()

        att = []
        for a in attachments:
            a = _decorate_attachment(a)
            att.append(a)

        return render_template("view_note.html", note=note, attachments=att, can_edit=can_edit)

    @app.route("/notes/<int:note_id>/pin", methods=["POST"])
    def pin_note(note_id):
        name = _current_db_name()
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        salt, phash = get_db_password_info(db_path)
        if salt and phash and not _is_unlocked(name) and not db_guest_can(name, "notes", "write"):
            return redirect(request.referrer or url_for("index"))

        db = get_db()
        note = db.execute("SELECT id, COALESCE(pinned, 0) AS pinned FROM notes WHERE id=?", (note_id,)).fetchone()
        if note is None:
            abort(404)

        # If pinned is explicitly provided, use it. Otherwise toggle.
        desired = (request.form.get("pinned") or "").strip()
        if desired in {"0", "1"}:
            pinned = int(desired)
        else:
            pinned = 0 if int(note["pinned"] or 0) else 1

        params = [pinned]
        sql = "UPDATE notes SET pinned=?"
        if _table_has_column(db, "notes", "updated_at"):
            sql += ", updated_at=?"
            params.append(iso_now())
        sql += " WHERE id=?"
        params.append(note_id)
        db.execute(sql, tuple(params))
        db.commit()

        nxt = request.form.get("next") or request.referrer or url_for("index")
        return redirect(nxt)

    @app.route("/notes/<int:note_id>/edit", methods=["GET", "POST"])
    def edit_note(note_id):
        # Read-only protection (password-protected DB that isn't unlocked)
        name = _current_db_name()
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        salt, phash = get_db_password_info(db_path)
        if salt and phash and not _is_unlocked(name) and not db_guest_can(name, "notes", "write"):
            return redirect(url_for("view_note", note_id=note_id))

        db = get_db()
        note = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if note is None:
            abort(404)

        if request.method == "GET":
            attachments = db.execute(
                "SELECT * FROM attachments WHERE note_id=? ORDER BY display_order ASC, id ASC", (note_id,)
            ).fetchall()
            att = []
            for a in attachments:
                a = _decorate_attachment(a)
                a["url"] = url_for("uploaded_file", filename=_upload_filename_for_db(a["stored_name"]))
                att.append(a)
            return render_template("note_form.html", is_edit=True, note=note, attachments=att)

        title = (request.form.get("title") or "").strip() or "(Untitled)"
        tag = normalize_tags((request.form.get("tag") or ""))
        content_html = request.form.get("content_html") or ""
        content_html = sanitize_note_html(content_html)
        content_delta = (request.form.get("content_delta") or "").strip()
        if content_delta:
            try:
                json.loads(content_delta)
            except Exception:
                content_delta = ""
        now = iso_now()

        # Backward compatible update (older DBs may still have 'description' column)
        has_desc = _table_has_column(db, "notes", "description")
        has_title = _table_has_column(db, "notes", "title")
        has_updated = _table_has_column(db, "notes", "updated_at")

        sets = []
        vals = []
        if has_title:
            sets.append("title=?")
            vals.append(title)
        if has_desc:
            sets.append("description=?")
            vals.append(title)
        sets.append("tag=?")
        vals.append(tag)
        sets.append("content_html=?")
        vals.append(content_html)
        if _table_has_column(db, "notes", "content_delta"):
            sets.append("content_delta=?")
            vals.append(content_delta)
        if has_updated:
            sets.append("updated_at=?")
            vals.append(now)
        vals.append(note_id)
        db.execute(f"UPDATE notes SET {', '.join(sets)} WHERE id=?", tuple(vals))

        # Update attachment order from JSON payload
        attachment_state = request.form.get("attachment_state") or ""
        if attachment_state.strip():
            try:
                state = json.loads(attachment_state)
                # expected: [{id, order}, ...]
                for item in state:
                    aid = int(item.get("id"))
                    order = int(item.get("order", 0))
                    new_name = (item.get("name") or "").strip()
                    icon_action = (item.get("icon_action") or "keep").strip().lower()
                    db.execute("UPDATE attachments SET display_order=? WHERE id=? AND note_id=?", (order, aid, note_id))
                    if new_name:
                        db.execute(
                            "UPDATE attachments SET original_name=? WHERE id=? AND note_id=?",
                            (new_name[:255], aid, note_id),
                        )
                    if _table_has_column(db, "attachments", "icon_stored_name"):
                        old = db.execute(
                            "SELECT icon_stored_name FROM attachments WHERE id=? AND note_id=?",
                            (aid, note_id),
                        ).fetchone()
                        old_icon = (old["icon_stored_name"] if old else "") or ""
                        icon_file = request.files.get(f"attachment_icon_file_{aid}")
                        icon_stored = ""
                        if icon_action == "clear":
                            db.execute(
                                "UPDATE attachments SET icon_stored_name=NULL WHERE id=? AND note_id=?",
                                (aid, note_id),
                            )
                        elif icon_action == "upload" and icon_file and getattr(icon_file, "filename", ""):
                            icon_stored = _save_attachment_icon_upload(icon_file)
                            if icon_stored:
                                db.execute(
                                    "UPDATE attachments SET icon_stored_name=? WHERE id=? AND note_id=?",
                                    (icon_stored, aid, note_id),
                                )
                        try:
                            if (icon_action == "clear" or (icon_stored and icon_stored != old_icon)) and old_icon:
                                p = current_upload_dir() / old_icon
                                if p.exists() and p.is_file():
                                    p.unlink()
                        except Exception:
                            pass
            except Exception:
                # Ignore malformed JSON; note content still saves
                pass

        # Add any new uploads (append to end)
        files = request.files.getlist("attachments")
        target_dir = current_upload_dir()  # avoid recomputing per-file
        for f in files:
            if f and f.filename:
                original = f.filename
                stored = unique_store_name(target_dir, original)
                ext = (Path(original).suffix or "").lower()
                if not _attachment_ext_allowed(original):
                    return redirect(url_for("edit_note", note_id=note_id, error=f"Blocked attachment type: {ext}"))
                max_bytes = int(get_attachment_max_bytes())
                ok, err = _save_with_size_limit(f, target_dir / stored, max_bytes)
                if not ok:
                    return redirect(
                        url_for(
                            "edit_note",
                            note_id=note_id,
                            error=f"Attachment too large. Max is {max_bytes // (1024*1024*1024)}GB.",
                        )
                    )
                db.execute(
                    "INSERT INTO attachments (note_id, original_name, stored_name, created_at, display_order) VALUES (?,?,?,?,?)",
                    (note_id, original, stored, now, next_attachment_order(db, note_id)),
                )
        db.commit()
        return redirect(url_for("view_note", note_id=note_id))

    @app.route("/notes/<int:note_id>/delete", methods=["POST"])
    def delete_note(note_id):
        # Read-only protection (password-protected DB that isn't unlocked)
        name = _current_db_name()
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        salt, phash = get_db_password_info(db_path)
        if salt and phash and not _is_unlocked(name) and not db_guest_can(name, "notes", "write"):
            return redirect(request.referrer or url_for("view_note", note_id=note_id))

        db = get_db()
        atts = db.execute(
            "SELECT stored_name, icon_stored_name FROM attachments WHERE note_id=?", (note_id,)
        ).fetchall()

        db.execute("DELETE FROM notes WHERE id=?", (note_id,))
        db.commit()

        upload_dir = current_upload_dir()
        for a in atts:
            for rel in [a["stored_name"], a["icon_stored_name"]]:
                if not rel:
                    continue
                try:
                    (upload_dir / rel).unlink(missing_ok=True)
                except Exception:
                    pass

        return redirect(url_for("index"))

    @app.route("/attachments/<int:att_id>/delete", methods=["POST"])
    def delete_attachment(att_id):
        """Delete a single attachment (only from its owning note)."""
        # Read-only protection (password-protected DB that isn't unlocked)
        name = _current_db_name()
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        salt, phash = get_db_password_info(db_path)
        if salt and phash and not _is_unlocked(name) and not db_guest_can(name, "notes", "write"):
            return redirect(request.referrer or url_for("index"))

        db = get_db()
        row = db.execute(
            "SELECT id, note_id, stored_name, icon_stored_name FROM attachments WHERE id=?", (att_id,)
        ).fetchone()
        if row is None:
            abort(404)

        note_id = int(row["note_id"])
        stored = row["stored_name"]
        icon = row["icon_stored_name"] or ""

        db.execute("DELETE FROM attachments WHERE id=?", (att_id,))
        db.commit()

        for rel in [stored, icon]:
            if not rel:
                continue
            try:
                (current_upload_dir() / rel).unlink(missing_ok=True)
            except Exception:
                pass

        return redirect(url_for("edit_note", note_id=note_id))
