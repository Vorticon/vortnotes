"""Bookmark/Links routes.

Bookmarks ("Links") are stored per selected database and rendered as a grid of
icons similar to attachments reordering.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from flask import abort, jsonify, redirect, render_template, request, url_for


def register_link_routes(app) -> None:
    # Late imports to avoid cycles.
    from ..webapp import (
        _attachment_ext_allowed,
        _current_db_name,
        _is_unlocked,
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

        def _wants_json() -> bool:
            try:
                if (request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest":
                    return True
                accept = (request.headers.get("Accept") or "").lower()
                return "application/json" in accept
            except Exception:
                return False

        if salt and phash and not _is_unlocked(name) and not db_guest_can(name, "content", "read"):
            # For AJAX/JSON fetch requests, return a JSON error instead of HTML redirect.
            if _wants_json():
                return jsonify({"ok": False, "error": "auth_required", "next": next_url}), 401
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

    def _normalize_url(raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        # Add scheme if user pasted "example.com"
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", raw):
            raw = "https://" + raw
        try:
            u = urlparse(raw)
        except Exception:
            return ""
        if u.scheme not in ("http", "https"):
            return ""
        # Normalize by dropping fragments; keep query.
        u = u._replace(fragment="")
        return urlunparse(u)

    def _default_title_for_url(u: str) -> str:
        try:
            host = urlparse(u).netloc
        except Exception:
            host = ""
        host = host.replace("www.", "")
        return host or u

    def _save_icon_upload(file_storage) -> str:
        if not file_storage or not getattr(file_storage, "filename", ""):
            return ""
        if not _attachment_ext_allowed(file_storage.filename):
            return ""
        # Save in a subfolder inside the per-DB upload namespace.
        icon_dir = current_upload_dir() / "link_icons"
        icon_dir.mkdir(parents=True, exist_ok=True)
        # unique_store_name expects (target_dir, original_filename)
        stored_name = unique_store_name(icon_dir, file_storage.filename)
        # Enforce a conservative icon size cap (reuse attachment max).
        max_bytes = int(get_attachment_max_bytes())
        data = file_storage.stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            return ""
        (icon_dir / stored_name).write_bytes(data)
        return f"link_icons/{stored_name}"

    def _save_group_icon_upload(file_storage) -> str:
        if not file_storage or not getattr(file_storage, "filename", ""):
            return ""
        if not _attachment_ext_allowed(file_storage.filename):
            return ""
        icon_dir = current_upload_dir() / "link_group_icons"
        icon_dir.mkdir(parents=True, exist_ok=True)
        stored_name = unique_store_name(icon_dir, file_storage.filename)
        max_bytes = int(get_attachment_max_bytes())
        data = file_storage.stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            return ""
        (icon_dir / stored_name).write_bytes(data)
        return f"link_group_icons/{stored_name}"

    def _try_fetch_favicon(page_url: str) -> str:
        """Attempt to fetch a site's favicon and store it under link_icons/."""
        u = _normalize_url(page_url)
        if not u:
            return ""
        parsed = urlparse(u)
        base = f"{parsed.scheme}://{parsed.netloc}"
        # Start with common default locations.
        candidates = [
            f"{base}/favicon.ico",
            f"{base}/favicon.png",
            f"{base}/apple-touch-icon.png",
            f"{base}/apple-touch-icon-precomposed.png",
        ]

        # Many sites define their icon via <link rel="icon" href="...">.
        # Best-effort: fetch the homepage HTML and extract any icon hrefs.
        try:
            req = Request(
                u,
                headers={
                    # Some sites (CDNs / WAFs) are picky about non-browser UAs.
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                },
            )
            # Keep this snappy; fetching a favicon should never block the UI for long.
            with urlopen(req, timeout=2) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "text/html" in ctype or "application/xhtml" in ctype or ctype == "":
                    html = resp.read(64 * 1024).decode("utf-8", errors="ignore")
                    # rel can be: icon, shortcut icon, apple-touch-icon, etc.
                    # Grab up to a few candidates in document order.
                    hrefs = re.findall(
                        r"<link[^>]+rel=\"([^\"]*icon[^\"]*)\"[^>]*href=\"([^\"]+)\"",
                        html,
                        flags=re.IGNORECASE,
                    )
                    extra = []
                    for _rel, href in hrefs[:4]:
                        href = (href or "").strip()
                        if not href:
                            continue
                        if href.startswith("//"):
                            full = f"{parsed.scheme}:{href}"
                        elif href.startswith("/"):
                            full = f"{base}{href}"
                        elif re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", href):
                            full = href
                        else:
                            full = f"{base}/{href.lstrip('./')}"
                        extra.append(full)
                    # Prefer declared icons first.
                    candidates = extra + candidates
        except Exception:
            pass

        icon_dir = current_upload_dir() / "link_icons"
        icon_dir.mkdir(parents=True, exist_ok=True)

        for cand in candidates:
            try:
                req = Request(
                    cand,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Accept": "image/*,*/*;q=0.5",
                        "Referer": base + "/",
                    },
                )
                with urlopen(req, timeout=2) as resp:
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    data = resp.read(256 * 1024 + 1)  # cap at 256KB
                    if len(data) > 256 * 1024:
                        continue
                    # Basic sanity: must look like an image or .ico
                    if ("image/" not in ctype) and ("icon" not in ctype) and (not cand.lower().endswith(".ico")):
                        continue
                    # Choose extension from URL, fall back to .ico
                    ext = Path(urlparse(cand).path).suffix.lower() or ".ico"
                    if ext not in (".ico", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"):
                        ext = ".ico"
                    stored_name = unique_store_name(icon_dir, f"favicon{ext}")
                    (icon_dir / stored_name).write_bytes(data)
                    return f"link_icons/{stored_name}"
            except Exception:
                continue
        return ""

    _embed_supported = None

    def _links_embed_supported() -> bool:
        """Return True if the current DB's links table has the `embed` column."""
        nonlocal _embed_supported
        if _embed_supported is not None:
            return bool(_embed_supported)
        try:
            cols = [r[1] for r in get_db().execute("PRAGMA table_info(links)").fetchall()]
            _embed_supported = 1 if "embed" in cols else 0
        except Exception:
            _embed_supported = 0
        return bool(_embed_supported)

    def _list_groups():
        db = get_db()
        return db.execute(
            "SELECT id, name, icon_stored_name, created_at, display_order "
            "FROM link_groups ORDER BY display_order ASC, id ASC"
        ).fetchall()

    def _list_links():
        db = get_db()
        # For editor usage we include group + sub-order.
        embed_sel = "l.embed" if _links_embed_supported() else "0 AS embed"
        rows = db.execute(
            "SELECT l.id, l.title, l.url, l.target, "
            + embed_sel
            + ", l.item_kind, l.icon_stored_name, l.file_stored_name, l.file_original_name, l.file_mime, l.file_size, l.created_at, l.display_order, l.group_id, l.sub_order, "
            "g.name AS group_name, g.display_order AS group_order "
            "FROM links l "
            "LEFT JOIN link_groups g ON g.id = l.group_id "
            "ORDER BY (l.group_id IS NOT NULL) ASC, g.display_order ASC, l.sub_order ASC, l.display_order ASC, l.id ASC"
        ).fetchall()
        return rows

    def _shift_orders(
        table: str, col: str, desired: int, where_sql: str, where_params: tuple, exclude_id: int | None = None
    ):
        """Make room at `desired` by shifting colliding rows down.

        SQLite UPDATE doesn't reliably support ORDER BY across all environments,
        so we do a deterministic select + per-row update (descending) instead.
        """
        if desired is None:
            return
        desired = int(desired)
        params = list(where_params)
        sql = f"SELECT id, {col} AS v FROM {table} WHERE {where_sql} AND {col} >= ?"
        params.append(desired)
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(int(exclude_id))
        sql += f" ORDER BY {col} DESC, id DESC"
        rows = get_db().execute(sql, tuple(params)).fetchall()
        for r in rows:
            get_db().execute(
                f"UPDATE {table} SET {col} = ? WHERE id = ?",
                (int(r["v"] or 0) + 1, int(r["id"])),
            )

    def _list_top_items(offset: int, limit: int):
        """Top-level icons for /links view: groups + ungrouped links + ungrouped files."""
        db = get_db()
        embed_sel = "COALESCE(embed,0) AS embed" if _links_embed_supported() else "0 AS embed"
        rows = db.execute(
            "SELECT 'group' AS item_type, id, name AS title, NULL AS url, NULL AS target, 0 AS embed, "
            "icon_stored_name, NULL AS item_kind, NULL AS file_mime, NULL AS file_original_name, NULL AS file_size, display_order AS ord "
            "FROM link_groups "
            "UNION ALL "
            "SELECT 'link' AS item_type, id, title, url, target, " + embed_sel + ", "
            "icon_stored_name, COALESCE(item_kind,'link') AS item_kind, file_mime, file_original_name, file_size, display_order AS ord "
            "FROM links WHERE group_id IS NULL "
            "ORDER BY ord ASC, item_type ASC, id ASC "
            "LIMIT ? OFFSET ?",
            (int(limit), int(offset)),
        ).fetchall()
        return rows

    def _count_top_items() -> int:
        db = get_db()
        g = db.execute("SELECT COUNT(*) AS c FROM link_groups").fetchone()
        links = db.execute("SELECT COUNT(*) AS c FROM links WHERE group_id IS NULL").fetchone()
        return int((g["c"] if g else 0) + (links["c"] if links else 0))

    def _group_links(rows, groups):
        """Return list of (group_id, group_name, links) in display order, including an Ungrouped bucket."""
        out = []
        by_gid = {}
        # Initialize with explicit groups
        for g in groups:
            by_gid[int(g["id"])] = {"id": int(g["id"]), "name": g["name"], "links": []}
        ungrouped = {"id": None, "name": "Ungrouped", "links": []}
        for r in rows:
            gid = r["group_id"]
            if gid is None:
                ungrouped["links"].append(r)
            else:
                bucket = by_gid.get(int(gid))
                if bucket is None:
                    # In case of orphaned group_id, treat as ungrouped.
                    ungrouped["links"].append(r)
                else:
                    bucket["links"].append(r)
        if ungrouped["links"]:
            out.append(ungrouped)
        for g in groups:
            bucket = by_gid.get(int(g["id"]))
            if bucket and bucket["links"]:
                out.append(bucket)
        return out

    @app.route("/links")
    def links():
        gate = _require_read_access(url_for("links"))
        if gate:
            return gate

        name = _current_db_name()
        can_edit = _is_unlocked(name) or db_guest_can(name, "content", "manage")
        # If DB has no password, allow edit too.
        db_path = resolve_db_path(name)
        salt, phash = get_db_password_info(db_path)
        if not (salt and phash):
            can_edit = True

        # View mode shows a single unified grid of top-level icons (groups + ungrouped links)
        # with pagination (32 per page).
        try:
            page = int(request.args.get("page", "1") or "1")
        except Exception:
            page = 1
        page = max(1, page)
        page_size = 32
        total = _count_top_items()
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * page_size

        groups = _list_groups()
        items = _list_top_items(offset, page_size)
        return render_template(
            "links.html",
            items=items,
            groups=groups,
            edit_mode=False,
            can_edit=can_edit,
            page=page,
            total_pages=total_pages,
        )

    @app.route("/links/edit", methods=["GET"])
    def links_edit():
        gate = _require_write_access(url_for("links_edit"))
        if gate:
            return gate

        # Editor is table-based; all edits go through /links/item/save and /links/item/delete.
        groups = _list_groups()
        rows = _list_links()

        # Build an ordered, flat list: each group row followed by its child links, then ungrouped links.
        out = []
        by_gid = {}
        for r in rows:
            if r["group_id"] is not None:
                by_gid.setdefault(int(r["group_id"]), []).append(r)

        for g in groups:
            out.append(
                {
                    "row_type": "group",
                    "id": int(g["id"]),
                    "title": g["name"],
                    "icon": g["icon_stored_name"],
                    "order_num": int(g["display_order"] or 0),
                }
            )
            children = sorted(by_gid.get(int(g["id"]), []), key=lambda x: (int(x["sub_order"] or 0), int(x["id"])))
            for link in children:
                kind = (link["item_kind"] if ("item_kind" in link.keys()) else "link") or "link"
                is_file = kind == "file"
                file_size = link["file_size"] if ("file_size" in link.keys()) else None
                size_mb = round((float(file_size) / (1024.0 * 1024.0)), 2) if (file_size is not None) else None
                out.append(
                    {
                        "row_type": "file" if is_file else "link",
                        "id": int(link["id"]),
                        "title": link["title"],
                        "url": link["url"],
                        "target": link["target"],
                        "embed": int(link["embed"] or 0),
                        "icon": link["icon_stored_name"],
                        "group_id": int(link["group_id"]),
                        "group_name": link["group_name"],
                        "order_num": int(link["display_order"] or 0),
                        "sub_order": int(link["sub_order"] or 0),
                        "item_kind": kind,
                        "file_mime": (link["file_mime"] if ("file_mime" in link.keys()) else None),
                        "file_original_name": (
                            link["file_original_name"] if ("file_original_name" in link.keys()) else None
                        ),
                        "file_size": int(file_size) if file_size is not None else None,
                        "file_size_mb": size_mb,
                    }
                )

        ungrouped = [r for r in rows if r["group_id"] is None]
        ungrouped.sort(key=lambda x: (int(x["display_order"] or 0), int(x["id"])))
        for link in ungrouped:
            kind = (link["item_kind"] if ("item_kind" in link.keys()) else "link") or "link"
            is_file = kind == "file"
            file_size = link["file_size"] if ("file_size" in link.keys()) else None
            size_mb = round((float(file_size) / (1024.0 * 1024.0)), 2) if (file_size is not None) else None
            out.append(
                {
                    "row_type": "file" if is_file else "link",
                    "id": int(link["id"]),
                    "title": link["title"],
                    "url": link["url"],
                    "target": link["target"],
                    "embed": int(link["embed"] or 0),
                    "icon": link["icon_stored_name"],
                    "group_id": None,
                    "group_name": "",
                    "order_num": int(link["display_order"] or 0),
                    "sub_order": None,
                    "item_kind": kind,
                    "file_mime": (link["file_mime"] if ("file_mime" in link.keys()) else None),
                    "file_original_name": (
                        link["file_original_name"] if ("file_original_name" in link.keys()) else None
                    ),
                    "file_size": int(file_size) if file_size is not None else None,
                    "file_size_mb": size_mb,
                }
            )

        # Optional filters (same approach as Notes page): repeated f_field / f_value query params.
        # Each filter is AND-ed together.
        raw_fields = request.args.getlist("f_field")
        raw_values = request.args.getlist("f_value")
        filters = []
        for i in range(min(len(raw_fields), len(raw_values))):
            ff = (raw_fields[i] or "all").strip().lower()
            fv = (raw_values[i] or "").strip()
            if fv:
                filters.append({"field": ff, "value": fv})

        if filters:

            def _row_matches_all(row):
                t = (row.get("title") or "").lower()
                u = (row.get("url") or "").lower()
                g = (row.get("group_name") or "").lower()
                ty = (row.get("row_type") or "").lower()

                for f in filters:
                    q = (f.get("value") or "").lower()
                    ff = f.get("field") or "all"
                    if ff == "title" and (q not in t):
                        return False
                    elif ff == "url" and (q not in u):
                        return False
                    elif ff == "group" and (q not in g):
                        return False
                    elif ff == "type" and (q not in ty):
                        return False
                    elif ff == "all":
                        if (q not in t) and (q not in u) and (q not in g) and (q not in ty):
                            return False
                return True

            out = [r for r in out if _row_matches_all(r)]
        # Paginate editor table if it grows large (32 rows per page)
        try:
            page = int(request.args.get("page", "1") or "1")
        except Exception:
            page = 1
        page = max(1, page)
        page_size = 32
        total = len(out)
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        start = (page - 1) * page_size
        end = start + page_size
        page_rows = out[start:end]

        return render_template(
            "links.html",
            groups=groups,
            editor_rows=page_rows,
            edit_mode=True,
            can_edit=True,
            page=page,
            total_pages=total_pages,
            filters=filters,
        )

    @app.route("/links/group/<int:group_id>/items")
    def links_group_items(group_id: int):
        gate = _require_read_access(url_for("links"))
        if gate:
            return gate

        try:
            page = int(request.args.get("page", "1") or "1")
        except Exception:
            page = 1
        page = max(1, page)
        page_size = 32
        offset = (page - 1) * page_size

        db = get_db()
        grow = db.execute(
            "SELECT id, name, icon_stored_name FROM link_groups WHERE id=?",
            (int(group_id),),
        ).fetchone()
        if not grow:
            return jsonify({"ok": False, "error": "not_found"}), 404

        total_row = db.execute(
            "SELECT COUNT(*) AS c FROM links WHERE group_id=?",
            (int(group_id),),
        ).fetchone()
        total = int(total_row["c"] if total_row else 0)
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
            offset = (page - 1) * page_size

        embed_sel = "COALESCE(embed,0) AS embed" if _links_embed_supported() else "0 AS embed"
        # Include file-related columns so the group popup can render files (image/video preview) correctly.
        links_rows = db.execute(
            "SELECT id, title, url, target, " + embed_sel + ", icon_stored_name, sub_order, "
            "item_kind, file_mime, file_original_name, file_size "
            "FROM links WHERE group_id=? "
            "ORDER BY sub_order ASC, id ASC "
            "LIMIT ? OFFSET ?",
            (int(group_id), int(page_size), int(offset)),
        ).fetchall()
        items = [
            {
                "id": int(r["id"]),
                "title": r["title"],
                "url": r["url"],
                "target": (r["target"] or "_blank"),
                "embed": int(r["embed"] or 0),
                "icon": r["icon_stored_name"],
                "sub_order": int(r["sub_order"] or 0),
                "item_kind": (r["item_kind"] or "link"),
                "file_mime": r["file_mime"],
                "file_original_name": r["file_original_name"],
                "file_size": int(r["file_size"] or 0) if (r["file_size"] is not None) else None,
            }
            for r in links_rows
        ]
        return jsonify(
            {
                "ok": True,
                "group": {
                    "id": int(grow["id"]),
                    "name": grow["name"],
                    "icon": grow["icon_stored_name"],
                },
                "items": items,
                "page": page,
                "total_pages": total_pages,
            }
        )

    @app.route("/links/item/save", methods=["POST"])
    def links_item_save():
        gate = _require_write_access(url_for("links_edit"))
        if gate:
            return gate

        db = get_db()

        mode = (request.form.get("mode") or "add").strip().lower()
        row_type = (request.form.get("row_type") or "link").strip().lower()
        rid_raw = (request.form.get("id") or "").strip()
        rid = int(rid_raw) if (rid_raw.isdigit()) else None

        def _int_or_none(v: str):
            v = (v or "").strip()
            if not v:
                return None
            try:
                return int(v)
            except Exception:
                return None

        order_num = _int_or_none(request.form.get("order_num"))
        sub_order = _int_or_none(request.form.get("sub_order"))
        embed = 1 if (request.form.get("embed") or "").strip() in ("1", "true", "on", "yes") else 0

        icon_action = (request.form.get("icon_action") or "keep").strip().lower()
        clear_icon = icon_action == "clear"

        if row_type == "group":
            name = (request.form.get("title") or "").strip()
            if not name:
                return redirect(url_for("links_edit"))

            # Order default: append
            if order_num is None:
                row = db.execute("SELECT COALESCE(MAX(display_order), -1) AS m FROM link_groups").fetchone()
                order_num = int(row["m"] if row and row["m"] is not None else -1) + 1

            # If the requested order conflicts, shift others down.
            _shift_orders(
                table="link_groups",
                col="display_order",
                desired=int(order_num),
                where_sql="1=1",
                where_params=(),
                exclude_id=rid if (mode == "edit" and rid is not None) else None,
            )

            icon_file = request.files.get("icon_file")
            icon_stored = ""
            old_icon = ""
            if mode == "edit" and rid is not None:
                ex = db.execute("SELECT icon_stored_name FROM link_groups WHERE id=?", (int(rid),)).fetchone()
                old_icon = (ex["icon_stored_name"] if ex else "") or ""

            if clear_icon:
                icon_stored = ""
            elif icon_action == "upload" and icon_file and getattr(icon_file, "filename", ""):
                icon_stored = _save_group_icon_upload(icon_file)

            if mode == "add":
                db.execute(
                    "INSERT INTO link_groups (name, icon_stored_name, created_at, display_order) VALUES (?,?,?,?)",
                    (name, icon_stored or None, iso_now(), int(order_num)),
                )
            elif mode == "edit" and rid is not None:
                if clear_icon:
                    db.execute(
                        "UPDATE link_groups SET name=?, icon_stored_name=NULL, display_order=? WHERE id=?",
                        (name, int(order_num), int(rid)),
                    )
                elif icon_stored:
                    db.execute(
                        "UPDATE link_groups SET name=?, icon_stored_name=?, display_order=? WHERE id=?",
                        (name, icon_stored, int(order_num), int(rid)),
                    )
                else:
                    db.execute(
                        "UPDATE link_groups SET name=?, display_order=? WHERE id=?",
                        (name, int(order_num), int(rid)),
                    )

                # Best-effort remove old icon if cleared or replaced.
                try:
                    if (clear_icon or (icon_stored and icon_stored != old_icon)) and old_icon:
                        p = current_upload_dir() / old_icon
                        if p.exists() and p.is_file():
                            p.unlink()
                except Exception:
                    pass

            db.commit()
            return redirect(url_for("links_edit"))

        if row_type == "file":
            # File item (stored in uploads; rendered in Content grid)
            title = (request.form.get("title") or "").strip()
            group_id = _int_or_none(request.form.get("group_id"))
            target = "_blank"

            # Existing file info (edit mode)
            old_icon = ""
            old_file_rel = ""
            if mode == "edit" and rid is not None:
                ex = db.execute(
                    "SELECT icon_stored_name, file_stored_name FROM links WHERE id=?",
                    (int(rid),),
                ).fetchone()
                old_icon = (ex["icon_stored_name"] if ex else "") or ""
                old_file_rel = (ex["file_stored_name"] if ex else "") or ""

            # Order defaults + conflict shifting
            if group_id is None:
                if order_num is None:
                    row = db.execute(
                        "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id IS NULL"
                    ).fetchone()
                    order_num = int(row["m"] if row and row["m"] is not None else -1) + 1
                sub_order_val = None
                _shift_orders(
                    table="links",
                    col="display_order",
                    desired=int(order_num),
                    where_sql="group_id IS NULL",
                    where_params=(),
                    exclude_id=rid if (mode == "edit" and rid is not None) else None,
                )
            else:
                if sub_order is None:
                    row = db.execute(
                        "SELECT COALESCE(MAX(sub_order), -1) AS m FROM links WHERE group_id=?", (int(group_id),)
                    ).fetchone()
                    sub_order = int(row["m"] if row and row["m"] is not None else -1) + 1
                sub_order_val = int(sub_order)
                if order_num is None:
                    order_num = 0
                _shift_orders(
                    table="links",
                    col="sub_order",
                    desired=int(sub_order_val),
                    where_sql="group_id = ?",
                    where_params=(int(group_id),),
                    exclude_id=rid if (mode == "edit" and rid is not None) else None,
                )

            # Icon handling (optional)
            icon_action = (request.form.get("icon_action") or "keep").strip().lower()
            clear_icon = icon_action == "clear"
            icon_file = request.files.get("icon_file")
            icon_stored = ""
            if clear_icon:
                icon_stored = ""
            elif icon_action == "upload" and icon_file and getattr(icon_file, "filename", ""):
                icon_stored = _save_icon_upload(icon_file)

            # File upload (required for add; optional for edit)
            f = request.files.get("file_file")
            file_rel = old_file_rel
            file_orig = None
            file_mime = None
            file_size = None
            if f and getattr(f, "filename", ""):
                if not _attachment_ext_allowed(f.filename):
                    return redirect(url_for("links_edit"))
                files_dir = current_upload_dir() / "content_files"
                files_dir.mkdir(parents=True, exist_ok=True)
                stored_name = unique_store_name(files_dir, f.filename)
                max_bytes = int(get_attachment_max_bytes())
                data = f.stream.read(max_bytes + 1)
                if len(data) > max_bytes:
                    return redirect(url_for("links_edit"))
                (files_dir / stored_name).write_bytes(data)
                file_rel = f"content_files/{stored_name}"
                file_orig = f.filename
                file_mime = (getattr(f, "mimetype", "") or "").strip() or "application/octet-stream"
                file_size = int(len(data))
                if not title:
                    title = f.filename

            if mode == "add":
                if not file_rel:
                    return redirect(url_for("links_edit"))
                db.execute(
                    "INSERT INTO links (title, url, group_id, sub_order, target, icon_stored_name, created_at, display_order, item_kind, file_stored_name, file_original_name, file_mime, file_size) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        title,
                        file_rel,
                        group_id,
                        sub_order_val,
                        target,
                        icon_stored or None,
                        iso_now(),
                        int(order_num),
                        "file",
                        file_rel,
                        file_orig or title,
                        file_mime,
                        file_size,
                    ),
                )
            elif mode == "edit" and rid is not None:
                # Keep existing file info unless a new upload was provided.
                if file_size is None:
                    ex2 = db.execute(
                        "SELECT file_original_name, file_mime, file_size FROM links WHERE id=?",
                        (int(rid),),
                    ).fetchone()
                    file_orig = ex2["file_original_name"] if ex2 else None
                    file_mime = ex2["file_mime"] if ex2 else None
                    file_size = ex2["file_size"] if ex2 else None

                if clear_icon:
                    db.execute(
                        "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, icon_stored_name=NULL, display_order=?, item_kind='file', file_stored_name=?, file_original_name=?, file_mime=?, file_size=? WHERE id=?",
                        (
                            title,
                            file_rel,
                            group_id,
                            sub_order_val,
                            target,
                            int(order_num),
                            file_rel,
                            file_orig,
                            file_mime,
                            file_size,
                            int(rid),
                        ),
                    )
                elif icon_stored:
                    db.execute(
                        "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, icon_stored_name=?, display_order=?, item_kind='file', file_stored_name=?, file_original_name=?, file_mime=?, file_size=? WHERE id=?",
                        (
                            title,
                            file_rel,
                            group_id,
                            sub_order_val,
                            target,
                            icon_stored,
                            int(order_num),
                            file_rel,
                            file_orig,
                            file_mime,
                            file_size,
                            int(rid),
                        ),
                    )
                else:
                    db.execute(
                        "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, display_order=?, item_kind='file', file_stored_name=?, file_original_name=?, file_mime=?, file_size=? WHERE id=?",
                        (
                            title,
                            file_rel,
                            group_id,
                            sub_order_val,
                            target,
                            int(order_num),
                            file_rel,
                            file_orig,
                            file_mime,
                            file_size,
                            int(rid),
                        ),
                    )

                # Remove old icon if cleared/replaced
                try:
                    if (clear_icon or (icon_stored and icon_stored != old_icon)) and old_icon:
                        p = current_upload_dir() / old_icon
                        if p.exists() and p.is_file():
                            p.unlink()
                except Exception:
                    pass

                # Remove old file if replaced
                try:
                    if file_rel and old_file_rel and file_rel != old_file_rel:
                        p = current_upload_dir() / old_file_rel
                        if p.exists() and p.is_file():
                            p.unlink()
                except Exception:
                    pass

            db.commit()
            return redirect(url_for("links_edit"))

        # link
        url = _normalize_url(request.form.get("url") or "")
        title = (request.form.get("title") or "").strip()
        if not url:
            return redirect(url_for("links_edit"))
        if not title:
            title = _default_title_for_url(url)

        target = (request.form.get("target") or "_blank").strip() or "_blank"
        if target not in ("_self", "_blank"):
            target = "_blank"

        group_id = _int_or_none(request.form.get("group_id"))

        # Order defaults
        if group_id is None:
            if order_num is None:
                row = db.execute(
                    "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id IS NULL"
                ).fetchone()
                order_num = int(row["m"] if row and row["m"] is not None else -1) + 1
            sub_order_val = None

            # If requested order conflicts among ungrouped links, shift others down.
            _shift_orders(
                table="links",
                col="display_order",
                desired=int(order_num),
                where_sql="group_id IS NULL",
                where_params=(),
                exclude_id=rid if (mode == "edit" and rid is not None) else None,
            )
        else:
            if sub_order is None:
                row = db.execute(
                    "SELECT COALESCE(MAX(sub_order), -1) AS m FROM links WHERE group_id=?", (int(group_id),)
                ).fetchone()
                sub_order = int(row["m"] if row and row["m"] is not None else -1) + 1
            sub_order_val = int(sub_order)
            if order_num is None:
                # keep existing or set 0; top-level order not used for grouped links
                order_num = 0

            # If requested sub_order conflicts within the same group, shift others down.
            _shift_orders(
                table="links",
                col="sub_order",
                desired=int(sub_order_val),
                where_sql="group_id = ?",
                where_params=(int(group_id),),
                exclude_id=rid if (mode == "edit" and rid is not None) else None,
            )

        icon_file = request.files.get("icon_file")
        old_icon = ""
        if mode == "edit" and rid is not None:
            ex = db.execute("SELECT icon_stored_name FROM links WHERE id=?", (int(rid),)).fetchone()
            old_icon = (ex["icon_stored_name"] if ex else "") or ""

        icon_stored = ""
        if clear_icon:
            icon_stored = ""
        elif icon_action == "upload" and icon_file and getattr(icon_file, "filename", ""):
            icon_stored = _save_icon_upload(icon_file)
        elif icon_action == "grab":
            icon_stored = _try_fetch_favicon(url)

        if mode == "add":
            db.execute(
                "INSERT INTO links (title, url, group_id, sub_order, target, icon_stored_name, created_at, display_order) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (title, url, group_id, sub_order_val, target, icon_stored or None, iso_now(), int(order_num)),
            )
            # set embed if supported (older DBs will ignore if column missing)
            try:
                new_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
                db.execute("UPDATE links SET embed=? WHERE id=?", (int(embed), int(new_id)))
            except Exception:
                pass
        elif mode == "edit" and rid is not None:
            if clear_icon:
                db.execute(
                    "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, embed=?, icon_stored_name=NULL, display_order=? WHERE id=?",
                    (title, url, group_id, sub_order_val, target, int(embed), int(order_num), int(rid)),
                )
            elif icon_stored:
                db.execute(
                    "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, embed=?, icon_stored_name=?, display_order=? WHERE id=?",
                    (title, url, group_id, sub_order_val, target, int(embed), icon_stored, int(order_num), int(rid)),
                )
            else:
                db.execute(
                    "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, embed=?, display_order=? WHERE id=?",
                    (title, url, group_id, sub_order_val, target, int(embed), int(order_num), int(rid)),
                )
            try:
                if (clear_icon or (icon_stored and icon_stored != old_icon)) and old_icon:
                    p = current_upload_dir() / old_icon
                    if p.exists() and p.is_file():
                        p.unlink()
            except Exception:
                pass

        db.commit()
        return redirect(url_for("links_edit"))

    @app.route("/links/item/delete", methods=["POST"])
    def links_item_delete():
        gate = _require_write_access(url_for("links_edit"))
        if gate:
            return gate

        sel = (request.form.get("selected") or "").strip()
        if not sel or ":" not in sel:
            return redirect(url_for("links_edit"))
        row_type, rid_raw = sel.split(":", 1)
        try:
            rid = int(rid_raw)
        except Exception:
            return redirect(url_for("links_edit"))

        db = get_db()
        if row_type == "group":
            row = db.execute("SELECT icon_stored_name FROM link_groups WHERE id=?", (int(rid),)).fetchone()
            icon = (row["icon_stored_name"] if row else "") or ""
            db.execute("UPDATE links SET group_id=NULL, sub_order=NULL WHERE group_id=?", (int(rid),))
            db.execute("DELETE FROM link_groups WHERE id=?", (int(rid),))
            db.commit()
            if icon:
                try:
                    p = current_upload_dir() / icon
                    if p.exists() and p.is_file():
                        p.unlink()
                except Exception:
                    pass
            return redirect(url_for("links_edit"))

        if row_type == "file":
            row = db.execute("SELECT icon_stored_name, file_stored_name FROM links WHERE id=?", (int(rid),)).fetchone()
            icon = (row["icon_stored_name"] if row else "") or ""
            f_rel = (row["file_stored_name"] if row else "") or ""
            db.execute("DELETE FROM links WHERE id=?", (int(rid),))
            db.commit()
            for rel in [icon, f_rel]:
                if not rel:
                    continue
                try:
                    p = current_upload_dir() / rel
                    if p.exists() and p.is_file():
                        p.unlink()
                except Exception:
                    pass
            return redirect(url_for("links_edit"))

        # link
        row = db.execute("SELECT icon_stored_name FROM links WHERE id=?", (int(rid),)).fetchone()
        icon = (row["icon_stored_name"] if row else "") or ""
        db.execute("DELETE FROM links WHERE id=?", (int(rid),))
        db.commit()
        if icon:
            try:
                p = current_upload_dir() / icon
                if p.exists() and p.is_file():
                    p.unlink()
            except Exception:
                pass
        return redirect(url_for("links_edit"))

    @app.route("/links/groups/rename/<int:group_id>", methods=["POST"])
    def links_group_rename(group_id: int):
        gate = _require_write_access(url_for("links_edit"))
        if gate:
            return gate

        name = (request.form.get("name") or "").strip()
        clear_icon = (request.form.get("clear_icon") or "").strip() == "1"
        icon_file = request.files.get("group_icon_file")

        if not name:
            return redirect(url_for("links_edit"))

        db = get_db()
        existing = db.execute("SELECT icon_stored_name FROM link_groups WHERE id=?", (group_id,)).fetchone()
        old_icon = (existing["icon_stored_name"] if existing else "") or ""

        icon_stored = ""
        if clear_icon:
            icon_stored = ""
        elif icon_file and getattr(icon_file, "filename", ""):
            icon_stored = _save_group_icon_upload(icon_file)
            if not icon_stored:
                # keep name update even if icon fails
                icon_stored = None  # signal no change

        # Update name, and optionally icon.
        if clear_icon:
            db.execute("UPDATE link_groups SET name=?, icon_stored_name=NULL WHERE id=?", (name, group_id))
        elif icon_stored is None:
            db.execute("UPDATE link_groups SET name=? WHERE id=?", (name, group_id))
        elif icon_stored:
            db.execute("UPDATE link_groups SET name=?, icon_stored_name=? WHERE id=?", (name, icon_stored, group_id))
        else:
            db.execute("UPDATE link_groups SET name=? WHERE id=?", (name, group_id))

        db.commit()

        # Best-effort remove old icon if cleared or replaced.
        try:
            if (clear_icon or (icon_stored and icon_stored != old_icon)) and old_icon:
                p = current_upload_dir() / old_icon
                if p.exists() and p.is_file():
                    p.unlink()
        except Exception:
            pass

        return redirect(url_for("links_edit"))

    @app.route("/links/groups/delete/<int:group_id>", methods=["POST"])
    def links_group_delete(group_id: int):
        gate = _require_write_access(url_for("links_edit"))
        if gate:
            return gate
        db = get_db()
        row = db.execute("SELECT icon_stored_name FROM link_groups WHERE id=?", (group_id,)).fetchone()
        icon = (row["icon_stored_name"] if row else "") or ""
        # Ungroup any links first.
        db.execute("UPDATE links SET group_id=NULL WHERE group_id=?", (group_id,))
        db.execute("DELETE FROM link_groups WHERE id=?", (group_id,))
        db.commit()

        # Best-effort remove group icon file if it exists.
        if icon:
            try:
                p = current_upload_dir() / icon
                if p.exists() and p.is_file():
                    p.unlink()
            except Exception:
                pass

        return redirect(url_for("links_edit"))

    @app.route("/links/delete/<int:link_id>", methods=["POST"])
    def links_delete(link_id: int):
        gate = _require_write_access(url_for("links_edit"))
        if gate:
            return gate

        db = get_db()
        row = db.execute("SELECT icon_stored_name FROM links WHERE id=?", (link_id,)).fetchone()
        if not row:
            abort(404)
        icon = row["icon_stored_name"] or ""

        db.execute("DELETE FROM links WHERE id=?", (link_id,))
        db.commit()

        # Best-effort remove icon file if it exists.
        if icon:
            try:
                p = current_upload_dir() / icon
                if p.exists() and p.is_file():
                    p.unlink()
            except Exception:
                pass

        return redirect(url_for("links_edit"))
