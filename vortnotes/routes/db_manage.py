"""Database management and admin routes.

Extracted from :mod:`vortnotes.webapp` to reduce module size.
We use a late import of :mod:`vortnotes.webapp` to access shared helpers without
introducing import cycles.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from flask import jsonify, make_response, redirect, render_template, request, send_file, session, url_for

from ..db import connect as db_connect
from ..settings import BASE_DIR, DATA_DIR, DB_DIR, UPLOAD_DIR

DB_PACKAGE_MANIFEST = "vortnotes-db-package.json"


def _safe_zip_member(name: str) -> bool:
    """Return True when a ZIP member is a relative, non-traversing path."""
    if not name:
        return False
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        return False
    normalized = normalized.rstrip("/")
    if not normalized:
        return False
    p = Path(normalized)
    if p.is_absolute():
        return False
    return all(part not in ("", ".", "..") for part in p.parts)


def _unique_db_import_name(preferred: str, existing: list[str], timestamp: str) -> str:
    preferred = (preferred or "imported.db").replace("\\", "/").rsplit("/", 1)[-1].strip()
    preferred = re.sub(r"[^A-Za-z0-9_.-]+", "_", preferred) or "imported.db"
    if not preferred.lower().endswith(".db"):
        preferred += ".db"
    stem = Path(preferred).stem or "imported"
    candidate = preferred
    if candidate not in existing:
        return candidate
    candidate = f"{stem}_import_{timestamp}.db"
    i = 1
    while candidate in existing:
        candidate = f"{stem}_import_{timestamp}_{i}.db"
        i += 1
    return candidate


def _unique_upload_key(preferred: str, upload_root: Path) -> str:
    base = (preferred or "imported_uploads").strip().replace("\\", "_").replace("/", "_")
    base = base or "imported_uploads"
    candidate = base
    i = 1
    while (upload_root / candidate).exists():
        candidate = f"{base}_{i}"
        i += 1
    return candidate


def register_db_manage_routes(app) -> None:
    # Late imports (register is called after vortnotes.webapp is fully initialized),
    # so this does not create an import cycle.
    from ..home_assistant import home_assistant_config
    from ..webapp import (
        ALLOWED_BG_EXTS,
        THEME_PRESETS,
        _current_db_name,
        _db_has_password,
        _is_admin_authed,
        _is_unlocked,
        _normalize_db_name,
        _safe_bg_filename,
        _save_with_size_limit,
        _set_admin_authed,
        _set_unlocked,
        _table_exists,
        _title_select_expr,
        admin_password_is_set,
        apply_upload_limits,
        clear_db_appearance,
        clear_db_password,
        db_upload_key,
        delete_db_appearance,
        ensure_db_initialized,
        fmt_dt,
        get_db_appearance,
        get_db_password_info,
        get_db_read_without_password,
        get_db_upload_key,
        get_inline_media_max_bytes,
        get_upload_limits_effective,
        is_image_filename,
        list_db_files,
        load_config,
        rename_db_appearance,
        resolve_db_path,
        save_config,
        selected_db_name,
        set_admin_password,
        set_db_appearance,
        set_db_password,
        set_db_read_without_password,
        set_db_upload_key,
        touch_db_last_access,
        upload_dir_for_db,
        verify_admin_password,
        verify_db_password,
    )

    BACKUP_DIR = DATA_DIR / "backups"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    def _db_package_bytes(name: str, stamp: str | None = None) -> tuple[io.BytesIO, str]:
        """Build the restorable DB package ZIP used by download/manual/auto backups."""
        name = _normalize_db_name(name)
        db_path = resolve_db_path(name)
        if not db_path.exists():
            raise FileNotFoundError(name)
        ensure_db_initialized(db_path)

        db_key = get_db_upload_key(db_path)
        upload_dir = UPLOAD_DIR / db_key
        appearance = get_db_appearance(name) or {}
        manifest = {
            "format": "vortnotes-db-package",
            "version": 1,
            "db_name": name,
            "database_path": f"database/{name}",
            "upload_key": db_key,
            "uploads_path": f"uploads/{db_key}",
            "appearance": appearance if isinstance(appearance, dict) else {},
        }

        mem = io.BytesIO()
        with tempfile.TemporaryDirectory() as td:
            snapshot_path = Path(td) / name
            src = sqlite3.connect(str(db_path))
            dest = sqlite3.connect(str(snapshot_path))
            try:
                src.backup(dest)
            finally:
                dest.close()
                src.close()

            with ZipFile(mem, "w", ZIP_DEFLATED) as z:
                z.writestr(DB_PACKAGE_MANIFEST, json.dumps(manifest, indent=2, sort_keys=True))
                z.write(snapshot_path, manifest["database_path"])
                if upload_dir.exists():
                    for p in upload_dir.rglob("*"):
                        if p.is_file():
                            rel = p.relative_to(UPLOAD_DIR)
                            z.write(p, str(Path("uploads") / rel))

        mem.seek(0)
        use_stamp = stamp or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_name = f"{name.replace('.db','')}_database_{use_stamp}.zip"
        return mem, out_name

    def _backup_path_for(name: str, kind: str = "manual", stamp: str | None = None) -> Path:
        db_name = _normalize_db_name(name)
        safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(db_name).stem) or "database"
        use_stamp = stamp or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return BACKUP_DIR / f"{safe_stem}_{kind}_{use_stamp}.zip"

    def _create_backup_zip(name: str, kind: str = "manual") -> Path:
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        mem, _download_name = _db_package_bytes(name, stamp=stamp)
        path = _backup_path_for(name, kind=kind, stamp=stamp)
        i = 1
        while path.exists():
            path = _backup_path_for(name, kind=f"{kind}_{i}", stamp=stamp)
            i += 1
        path.write_bytes(mem.getvalue())
        return path

    def _backup_entries() -> list[dict]:
        entries = []
        if not BACKUP_DIR.exists():
            return entries
        for p in sorted(BACKUP_DIR.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                size = int(p.stat().st_size)
                modified = datetime.utcfromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
            except Exception:
                size = 0
                modified = ""
            entries.append(
                {
                    "name": p.name,
                    "size": size,
                    "size_mb": round(size / (1024 * 1024), 2),
                    "modified": fmt_dt(modified) if modified else "",
                }
            )
        return entries

    def _saved_backup_path(raw_name: str) -> Path | None:
        name = Path((raw_name or "").strip()).name
        if not name.lower().endswith(".zip"):
            return None
        path = BACKUP_DIR / name
        try:
            if not path.exists() or not path.is_file():
                return None
            if path.resolve().parent != BACKUP_DIR.resolve():
                return None
        except Exception:
            return None
        return path

    def _prune_auto_backups(name: str, keep: int = 3) -> None:
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(_normalize_db_name(name)).stem) or "database"
        backups = sorted(
            BACKUP_DIR.glob(f"{stem}_auto_*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[int(keep) :]:
            try:
                old.unlink()
            except Exception:
                pass

    def _disk_stats() -> dict:
        total, used, free = shutil.disk_usage(DATA_DIR)
        return {
            "path": str(DATA_DIR),
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "used_pct": round((used / total) * 100, 1) if total else 0,
        }

    def _path_usage(path: Path) -> dict:
        total = 0
        files = 0
        try:
            if path.exists() and path.is_file():
                return {"bytes": int(path.stat().st_size), "files": 1}
            if path.exists():
                for p in path.rglob("*"):
                    try:
                        if p.is_file():
                            total += int(p.stat().st_size)
                            files += 1
                    except Exception:
                        pass
        except Exception:
            pass
        return {"bytes": total, "files": files}

    def _fmt_size(num_bytes: int) -> str:
        size = float(num_bytes or 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024
        return f"{size:.2f} TB"

    def _storage_stats() -> dict:
        db_rows = []
        db_total = 0
        upload_total = 0
        for name in list_db_files():
            db_path = resolve_db_path(name)
            db_usage = _path_usage(db_path)
            upload_path = UPLOAD_DIR / get_db_upload_key(db_path)
            upload_usage = _path_usage(upload_path)
            total_bytes = int(db_usage["bytes"]) + int(upload_usage["bytes"])
            db_total += int(db_usage["bytes"])
            upload_total += int(upload_usage["bytes"])
            db_rows.append(
                {
                    "name": name,
                    "db_size": _fmt_size(int(db_usage["bytes"])),
                    "upload_size": _fmt_size(int(upload_usage["bytes"])),
                    "upload_files": int(upload_usage["files"]),
                    "total_size": _fmt_size(total_bytes),
                    "total_bytes": total_bytes,
                }
            )
        db_rows.sort(key=lambda r: r["name"].lower())

        data_usage = _path_usage(DATA_DIR)
        backup_usage = _path_usage(BACKUP_DIR)
        config_usage = _path_usage(DATA_DIR / "config")
        logs_usage = _path_usage(DATA_DIR / "logs")
        other_bytes = max(
            0,
            int(data_usage["bytes"])
            - db_total
            - upload_total
            - int(backup_usage["bytes"])
            - int(config_usage["bytes"])
            - int(logs_usage["bytes"]),
        )
        return {
            "disk": _disk_stats(),
            "rows": db_rows,
            "total_size": _fmt_size(int(data_usage["bytes"])),
            "total_files": int(data_usage["files"]),
            "db_size": _fmt_size(db_total),
            "uploads_size": _fmt_size(upload_total),
            "backups_size": _fmt_size(int(backup_usage["bytes"])),
            "backups_files": int(backup_usage["files"]),
            "config_size": _fmt_size(int(config_usage["bytes"])),
            "logs_size": _fmt_size(int(logs_usage["bytes"])),
            "other_size": _fmt_size(other_bytes),
            "db_dir": str(DB_DIR),
            "uploads_dir": str(UPLOAD_DIR),
        }

    def _auto_backup_config() -> dict:
        cfg = load_config()
        ab = cfg.get("auto_backup")
        if not isinstance(ab, dict):
            ab = {}
        last_run = str(ab.get("last_run") or "")
        last_run_display = ""
        if last_run:
            try:
                last_run_display = fmt_dt(last_run)
            except Exception:
                last_run_display = last_run
        return {
            "enabled": bool(ab.get("enabled")),
            "interval_hours": max(1, int(ab.get("interval_hours") or 24)),
            "last_run": str(ab.get("last_run") or ""),
            "last_run_display": last_run_display,
        }

    def _maybe_run_auto_backup() -> None:
        ab = _auto_backup_config()
        if not ab["enabled"]:
            return
        try:
            last = datetime.fromisoformat(ab["last_run"]) if ab["last_run"] else None
        except Exception:
            last = None
        now = datetime.utcnow()
        if last and (now - last).total_seconds() < int(ab["interval_hours"]) * 3600:
            return
        for db_name in list_db_files():
            try:
                _create_backup_zip(db_name, kind="auto")
                _prune_auto_backups(db_name, keep=3)
            except Exception:
                pass
        cfg = load_config()
        cfg["auto_backup"] = {
            "enabled": True,
            "interval_hours": int(ab["interval_hours"]),
            "last_run": now.isoformat(timespec="seconds"),
        }
        save_config(cfg)

    def _run_auto_backups_now() -> None:
        now = datetime.utcnow()
        for db_name in list_db_files():
            _create_backup_zip(db_name, kind="auto")
            _prune_auto_backups(db_name, keep=3)
        cfg = load_config()
        ab = _auto_backup_config()
        cfg["auto_backup"] = {
            "enabled": bool(ab["enabled"]),
            "interval_hours": int(ab["interval_hours"]),
            "last_run": now.isoformat(timespec="seconds"),
        }
        save_config(cfg)

    @app.before_request
    def _auto_backup_before_request():
        try:
            _maybe_run_auto_backup()
        except Exception:
            pass

    @app.route("/db/admin-login", methods=["GET", "POST"])
    def db_admin_login():
        error = None
        setup_required = not admin_password_is_set()
        if request.method == "POST":
            if setup_required:
                pwd = (request.form.get("password") or "").strip()
                confirm = (request.form.get("password_confirm") or "").strip()
                if not pwd:
                    error = "Admin password cannot be empty."
                elif pwd != confirm:
                    error = "Admin password confirmation does not match."
                else:
                    set_admin_password(pwd)
                    _set_admin_authed(remember=True)
                    return redirect(request.form.get("next") or url_for("settings_page"))
            else:
                provided = request.form.get("password") or ""
                remember = bool(request.form.get("remember"))
                if verify_admin_password(provided):
                    _set_admin_authed(remember=remember)
                    return redirect(request.form.get("next") or url_for("settings_page"))
                error = "Incorrect password."
        next_url = request.values.get("next") or url_for("settings_page")
        return render_template(
            "admin_login.html",
            error=error,
            next_url=next_url,
            is_set=admin_password_is_set(),
            setup_required=setup_required,
        )

    @app.route("/db/admin-logout", methods=["POST"])
    def db_admin_logout():
        session.pop("admin_authed", None)
        return redirect(url_for("index"))

    @app.route("/logout", methods=["POST"])
    def logout():
        """Log out only the currently selected database session."""
        name = _normalize_db_name((request.form.get("name") or selected_db_name()).strip())
        if name not in list_db_files():
            name = selected_db_name()
        unlocked = session.get("unlocked_dbs", {})
        if isinstance(unlocked, dict):
            unlocked.pop(name, None)
            session["unlocked_dbs"] = unlocked
        if session.get("remember_all_dbs"):
            overrides = session.get("db_logout_overrides", {})
            overrides = overrides if isinstance(overrides, dict) else {}
            overrides[name] = True
            session["db_logout_overrides"] = overrides
        resp = make_response(redirect(url_for("settings_page", name=name)))
        resp.set_cookie(
            "selected_db",
            name,
            max_age=int(timedelta(days=365).total_seconds()),
            samesite="Lax",
        )
        return resp

    @app.before_request
    def require_db_admin():
        p = request.path or ""
        if not p.startswith("/db"):
            return
        # allow static
        if p.startswith("/db/admin-login") or p.startswith("/db/admin-logout"):
            return
        # DB selection should be accessible without admin login.
        # The admin password is only meant to protect Database Management actions.
        if p.startswith("/db/select"):
            return
        # Keep the old /db URL as a compatibility redirect into unified Settings.
        if p == "/db" and request.method == "GET":
            return
        # IMPORTANT: DB unlock must remain accessible without the admin password.
        # Otherwise a DB-password-protected home page cannot be unlocked after
        # cookies/session are cleared (the unlock POST would get redirected to
        # the admin login, trapping the user).
        if p.startswith("/db/unlock"):
            return
        if not _is_admin_authed():
            # For fetch/AJAX requests, a redirect results in HTML being returned where
            # callers expect JSON. Detect JSON intent and return a structured error so
            # the UI can send the user to the admin login page.
            accept = (request.headers.get("Accept") or "").lower()
            xreq = (request.headers.get("X-Requested-With") or "").lower()
            wants_json = ("application/json" in accept) or (xreq in ("xmlhttprequest", "fetch"))
            login_url = url_for("db_admin_login", next=request.full_path)
            if wants_json:
                return jsonify({"ok": False, "error": "Admin login required.", "login_url": login_url}), 401
            return redirect(login_url)

    @app.route("/db", methods=["GET"])
    def db_manage():
        if request.path == "/db":
            return redirect(url_for("settings_page", **request.args))

        # "Default" DB is global and is used when a browser has no selected_db cookie.
        cfg = load_config()
        default_db = _normalize_db_name(cfg.get("default_db") or "notes.db")
        dbs = list_db_files()
        password_set = {}
        for n in dbs:
            p = resolve_db_path(n)
            ensure_db_initialized(p)
            password_set[n] = _db_has_password(p)
        # Build appearance map for UI display
        theme_map = {}
        for n in dbs:
            a = get_db_appearance(n) or {}
            preset_raw = (a.get("preset") or "").strip().lower()
            accent = (a.get("accent") or "").strip()
            accent2 = (a.get("accent2") or "").strip()

            mode = "default"  # default | preset | custom
            preset_display = preset_raw or "default"

            if preset_raw:
                # preset stored (including "default")
                if preset_raw in THEME_PRESETS:
                    p1, p2 = THEME_PRESETS[preset_raw]
                    if not accent:
                        accent = p1
                    if not accent2:
                        accent2 = p2
                    mode = "preset" if preset_raw != "default" else "default"
                else:
                    # unknown preset stored; treat as default
                    preset_display = "default"
                    mode = "default"
            else:
                # no preset stored -> custom colors (if any)
                mode = "custom" if (accent or accent2) else "default"
                preset_display = "default"

            bg_filename = (a.get("bg_filename") or "").strip()
            # Optional UI toggles (stored per DB)
            try:
                btn_hover_outline = int(a.get("btn_hover_outline") or 0)
            except Exception:
                btn_hover_outline = 0
            btn_hover_outline = 1 if btn_hover_outline else 0
            # Background brightness slider value (0-100). Stored per DB when a background exists.
            try:
                bg_brightness = int(a.get("bg_brightness") or 10)
            except Exception:
                bg_brightness = 10
            bg_brightness = max(0, min(100, bg_brightness))
            theme_map[n] = {
                "mode": mode,
                "preset": preset_display,
                "accent": accent or "",
                "accent2": accent2 or "",
                "has_bg": bool(bg_filename),
                "bg_filename": bg_filename,
                "bg_brightness": bg_brightness,
                "btn_hover_outline": btn_hover_outline,
            }

        # Permissions + last accessed (stored per DB)
        last_access_map = {}
        perm_read_map = {}
        for n in dbs:
            a = get_db_appearance(n) or {}
            last_access_map[n] = fmt_dt(str(a.get("last_access") or ""))
            perm_read_map[n] = 1 if get_db_read_without_password(n) else 0
        selected_db = selected_db_name()

        return render_template(
            "db.html",
            dbs=dbs,
            default_db=default_db,
            selected_db=selected_db,
            last_access_map=last_access_map,
            perm_read_map=perm_read_map,
            password_set=password_set,
            theme_presets=["default"] + sorted(THEME_PRESETS.keys()),
            theme_map=theme_map,
            admin_password_set=admin_password_is_set(),
            admin_password_is_set=admin_password_is_set(),
            error_msg=(request.args.get("error") or "").strip(),
            upload_cfg=get_upload_limits_effective(),
            backup_entries=_backup_entries(),
            auto_backup=_auto_backup_config(),
            disk_stats=_disk_stats(),
            storage_stats=_storage_stats(),
            home_assistant=home_assistant_config(load_config()),
        )

    @app.route("/db/theme-set", methods=["POST"])
    def db_theme_set():
        db_name = _normalize_db_name((request.form.get("db_name") or "").strip())
        if not db_name:
            return redirect(url_for("db_manage", error="Database name is required."))
        if db_name not in list_db_files():
            return redirect(url_for("db_manage", error="Database not found."))

        # preset:
        #   ""  -> custom colors
        #   "default" -> default colors (but can still have a background image)
        #   <preset> -> use THEME_PRESETS
        preset = (request.form.get("preset") or "").strip().lower()
        if preset and preset not in THEME_PRESETS and preset != "default":
            return redirect(url_for("db_manage", error="Unknown theme preset."))

        accent = (request.form.get("accent") or "").strip()
        accent2 = (request.form.get("accent2") or "").strip()
        # Optional: add a crisp hover outline on buttons (0/1)
        # Note: the form includes a hidden input with value=0 plus a checkbox with value=1.
        # Flask's request.form.get() returns the first value for a repeated key (often the hidden 0),
        # so we must use getlist() to detect the checked state.
        vals = [v.strip() for v in request.form.getlist("btn_hover_outline") if v is not None]
        btn_hover_outline = 1 if ("1" in vals or "true" in [v.lower() for v in vals]) else 0
        clear_bg = bool(request.form.get("clear_bg"))
        bg_file = request.files.get("bg_file")
        # 0-100 (higher = brighter background image)
        try:
            bg_brightness = int((request.form.get("bg_brightness") or "").strip() or 10)
        except Exception:
            bg_brightness = 10
        bg_brightness = max(0, min(100, bg_brightness))

        current = get_db_appearance(db_name) or {}
        bg_filename = (current.get("bg_filename") or "").strip()

        # Handle background removal / replace
        try:
            bg_dir = upload_dir_for_db(db_name)
            if clear_bg and bg_filename:
                try:
                    (bg_dir / bg_filename).unlink(missing_ok=True)
                except Exception:
                    pass
                bg_filename = ""
            if bg_file and getattr(bg_file, "filename", ""):
                ext = (Path(bg_file.filename).suffix or "").lower()
                if ext not in ALLOWED_BG_EXTS:
                    return redirect(
                        url_for("db_manage", error="Unsupported background image type. Use PNG/JPG/WEBP/GIF.")
                    )
                new_name = _safe_bg_filename(db_name, ext)
                save_path = bg_dir / new_name
                max_bytes = int(get_inline_media_max_bytes())
                ok, err = _save_with_size_limit(bg_file, save_path, max_bytes)
                if not ok:
                    return redirect(
                        url_for("db_manage", error=f"Background image too large. Max is {max_bytes // (1024*1024)}MB.")
                    )
                bg_filename = new_name
        except Exception:
            # If background can't be saved, don't block theme changes
            pass

        # IMPORTANT: appearance also stores per-DB meta/permissions (e.g. last_access,
        # perm_read_no_password). Theme saves must NOT wipe those keys.
        appearance = dict(current) if isinstance(current, dict) else {}

        # Remove theme-related keys first, then re-apply what the user selected.
        # (This allows clearing old values while preserving meta/permission keys.)
        for k in ("preset", "accent", "accent2", "bg_filename", "bg_brightness", "btn_hover_outline"):
            appearance.pop(k, None)

        if preset:
            # Preset selected (including "default")
            appearance["preset"] = preset
        else:
            # Custom colors
            if accent:
                appearance["accent"] = accent
            if accent2:
                appearance["accent2"] = accent2

        if bg_filename:
            appearance["bg_filename"] = bg_filename

        # Persist the hover-outline toggle even with default colors.
        appearance["btn_hover_outline"] = btn_hover_outline

        # Only persist brightness when a background image exists (otherwise it's unused).
        if bg_filename:
            appearance["bg_brightness"] = bg_brightness

        # If the user effectively cleared theme settings, do NOT wipe the whole
        # appearance entry (it may contain permissions/last_access). Instead,
        # delete only theme keys by leaving them absent.

        # If after update there are no keys left at all, we can remove the entry.
        if not appearance:
            clear_db_appearance(db_name)
            return redirect(url_for("db_manage"))

        set_db_appearance(db_name, appearance)
        return redirect(url_for("db_manage"))

    @app.route("/db/config-set", methods=["POST"])
    def db_config_set():
        """Update global (non-DB-specific) app configuration."""
        cfg = load_config()
        ul = cfg.get("upload_limits")
        ul = ul if isinstance(ul, dict) else {}

        def _to_int(key: str, default: int) -> int:
            try:
                return int((request.form.get(key) or "").strip() or default)
            except Exception:
                return int(default)

        max_request_mb = max(1, _to_int("max_request_mb", 50))
        # Per-file size for attachments/media (MB)
        attachment_max_mb = max(0, _to_int("attachment_max_mb", 50))
        inline_media_max_mb = max(1, _to_int("inline_media_max_mb", 50))

        ul["max_request_mb"] = max_request_mb
        ul["attachment_max_mb"] = attachment_max_mb
        # Drop legacy key if present
        try:
            ul.pop("attachment_max_gb", None)
        except Exception:
            pass
        ul["inline_media_max_mb"] = inline_media_max_mb
        cfg["upload_limits"] = ul
        save_config(cfg)

        # Apply immediately for this running instance.
        try:
            apply_upload_limits()
        except Exception:
            pass

        return redirect(url_for("settings_page", notice="Upload configuration saved."))

    @app.route("/db/auto-backup-set", methods=["POST"])
    def db_auto_backup_set():
        enabled = (request.form.get("enabled") or "").strip() in ("1", "true", "True", "on", "yes")
        try:
            interval_hours = max(1, int((request.form.get("interval_hours") or "24").strip()))
        except Exception:
            interval_hours = 24

        cfg = load_config()
        prev = cfg.get("auto_backup") if isinstance(cfg.get("auto_backup"), dict) else {}
        cfg["auto_backup"] = {
            "enabled": enabled,
            "interval_hours": interval_hours,
            "last_run": str(prev.get("last_run") or ""),
        }
        save_config(cfg)

        if request.form.get("run_now"):
            try:
                _run_auto_backups_now()
            except Exception:
                return redirect(url_for("db_manage", error="Auto backup failed."))

        return redirect(url_for("db_manage", error="Auto-backup settings saved."))

    @app.route("/db/perm-read-set", methods=["POST"])
    def db_perm_read_set():
        db_name = _normalize_db_name((request.form.get("db_name") or "").strip())
        enabled_raw = (request.form.get("enabled") or "").strip()
        enabled = enabled_raw in ("1", "true", "True", "on", "yes")
        if not db_name:
            return jsonify({"ok": False, "error": "Database name is required."}), 400
        if db_name not in list_db_files():
            return jsonify({"ok": False, "error": "Database not found."}), 404

        # Only meaningful for password-protected DBs, but we still store the flag either way.
        set_db_read_without_password(db_name, enabled)
        return jsonify({"ok": True, "enabled": 1 if enabled else 0})

    @app.route("/db/admin-set-password", methods=["POST"])
    def db_admin_set_password():
        pwd = (request.form.get("password") or "").strip()
        confirm = (request.form.get("password_confirm") or "").strip()
        if not pwd:
            return redirect(url_for("db_manage", error="Admin password cannot be empty."))
        if pwd != confirm:
            return redirect(url_for("db_manage", error="Admin password confirmation does not match."))
        set_admin_password(pwd)
        _set_admin_authed(remember=True)
        return redirect(url_for("db_manage"))

    @app.route("/db/create", methods=["POST"])
    def db_create():
        name = (request.form.get("name") or "").strip()
        if not name:
            return redirect(url_for("db_manage"))
        if not name.endswith(".db"):
            name += ".db"

        # Enforce a maximum of 10 DBs.
        existing = list_db_files()
        if name not in existing and len(existing) >= 10:
            return redirect(url_for("db_manage", error="Maximum of 10 databases allowed."))

        p = resolve_db_path(name)
        ensure_db_initialized(p)
        return redirect(url_for("db_manage"))

    @app.route("/db/rename", methods=["POST"])
    def db_rename():
        old = (request.form.get("old") or "").strip()
        new = (request.form.get("new") or "").strip()
        if not old or not new:
            return redirect(url_for("db_manage"))
        if not old.endswith(".db"):
            old += ".db"
        if not new.endswith(".db"):
            new += ".db"
        old_path = resolve_db_path(old)
        new_path = resolve_db_path(new)
        if new_path.exists():
            return ("DB already exists.", 400)
        old_path.rename(new_path)
        rename_db_appearance(old, new)
        # If the renamed DB is currently selected for this browser, update the cookie.
        selected = selected_db_name() == old
        unlocked = session.get("unlocked_dbs", {})
        if unlocked.get(old):
            unlocked[new] = True
            unlocked.pop(old, None)
            session["unlocked_dbs"] = unlocked
        resp = make_response(redirect(url_for("db_manage")))
        if selected:
            resp.set_cookie(
                "selected_db",
                new,
                max_age=60 * 60 * 24 * 365,
                samesite="Lax",
            )
        return resp

    @app.route("/db/delete", methods=["POST"])
    def db_delete():
        name = (request.form.get("name") or "").strip()
        if not name:
            return redirect(url_for("db_manage"))
        if not name.endswith(".db"):
            name += ".db"
        if selected_db_name() == name:
            return ("Cannot delete selected DB. Select another first.", 400)
        path = resolve_db_path(name)
        if path.exists():
            path.unlink()
        delete_db_appearance(name)
        return redirect(url_for("db_manage"))

    @app.route("/db/backup", methods=["POST"])
    def db_backup():
        """Create a restorable DB package ZIP, save it to backups, and download it."""
        name = (request.form.get("name") or "").strip()
        if not name:
            return redirect(url_for("db_manage"))
        if not name.endswith(".db"):
            name += ".db"

        src = resolve_db_path(name)
        if not src.exists():
            return redirect(url_for("db_manage", error="Database not found."))

        try:
            backup_path = _create_backup_zip(name, kind="manual")
        except Exception:
            return redirect(url_for("db_manage", error="Backup failed."))
        return send_file(backup_path, mimetype="application/zip", as_attachment=True, download_name=backup_path.name)

    @app.route("/db/backup/download/<path:backup_name>", methods=["GET"], endpoint="db_backup_download")
    def db_backup_download(backup_name: str):
        backup_path = _saved_backup_path(backup_name)
        if not backup_path:
            return redirect(url_for("db_manage", error="Backup not found."))
        return send_file(backup_path, mimetype="application/zip", as_attachment=True, download_name=backup_path.name)

    @app.route("/db/backup/delete", methods=["POST"], endpoint="db_backup_delete")
    def db_backup_delete():
        backup_path = _saved_backup_path(request.form.get("backup_name") or "")
        if not backup_path:
            return redirect(url_for("db_manage", error="Backup not found."))
        try:
            backup_path.unlink()
        except Exception:
            return redirect(url_for("db_manage", error="Backup delete failed."))
        return redirect(url_for("db_manage", error="Backup deleted."))

    @app.route("/db/set-default", methods=["POST"])
    def db_set_default():
        name = _normalize_db_name(request.form.get("name"))
        if not name:
            return redirect(url_for("db_manage"))
        # ensure it exists
        ensure_db_initialized(resolve_db_path(name))

        # Persist as the global default (used when no cookie is set)
        cfg = load_config()
        cfg["default_db"] = name
        save_config(cfg)

        # Also set the per-browser selection cookie.
        # If the DB is password-protected, the user will be prompted on next navigation.
        resp = make_response(redirect(url_for("db_manage")))

        resp.set_cookie(
            "selected_db",
            name,
            max_age=60 * 60 * 24 * 365,  # 1 year
            samesite="Lax",
        )
        return resp

    @app.route("/db/set-password", methods=["POST"])
    def db_set_password():
        name = (request.form.get("name") or "").strip()
        new_pwd = (request.form.get("password") or "").strip()
        if not name or not new_pwd:
            return redirect(url_for("db_manage"))
        if not name.endswith(".db"):
            name += ".db"
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        set_db_password(db_path, new_pwd)
        unlocked = session.get("unlocked_dbs", {})
        unlocked.pop(name, None)
        session["unlocked_dbs"] = unlocked
        return redirect(url_for("db_manage"))

    @app.route("/db/clear-password", methods=["POST"])
    def db_clear_password():
        name = (request.form.get("name") or "").strip()
        if not name:
            return redirect(url_for("db_manage"))
        if not name.endswith(".db"):
            name += ".db"
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        clear_db_password(db_path)
        unlocked = session.get("unlocked_dbs", {})
        unlocked.pop(name, None)
        session["unlocked_dbs"] = unlocked
        return redirect(url_for("db_manage"))

    @app.route("/db/unlock", methods=["GET", "POST"])
    def db_unlock():
        """Backward-compatible route: redirect to the unified DB selection screen."""
        name = (request.values.get("name") or "").strip()
        next_url = request.values.get("next")
        args = {}
        if name:
            args["name"] = name
        if next_url:
            args["next"] = next_url
        return redirect(url_for("settings_page", **args))

    @app.route("/db/select", methods=["GET", "POST"])
    def db_select():
        """Unified DB selector + optional unlock.

        - If DB has no password: password field is disabled.
        - If DB has a password:
            * If "read without password" is enabled for that DB, user may open without a password (read-only).
            * User may provide password to unlock full access.
        """

        # Name comes from query param (dropdown change), form submit, or current cookie.
        raw_name = (request.values.get("name") or _current_db_name() or "").strip()
        if raw_name and not raw_name.endswith(".db"):
            raw_name += ".db"
        name = raw_name or _current_db_name()

        next_url = request.values.get("next") or url_for("index")
        dbs = list_db_files()

        if request.method == "GET":
            args = {}
            if name:
                args["name"] = name
            if next_url:
                args["next"] = next_url
            return redirect(url_for("settings_page", **args))

        # Build light metadata so the UI can show the correct state when selecting DBs.
        db_meta = {}
        for n in dbs:
            try:
                p = resolve_db_path(n)
                ensure_db_initialized(p)
                salt, phash = get_db_password_info(p)
                db_meta[n] = {
                    "has_password": bool(salt and phash),
                    "read_without_password": bool(get_db_read_without_password(n)),
                }
            except Exception:
                db_meta[n] = {"has_password": False, "read_without_password": False}

        error = None

        def _resp_with_db_cookie(resp, selected_name: str):
            resp.set_cookie(
                "selected_db",
                selected_name,
                max_age=int(timedelta(days=365).total_seconds()),
                samesite="Lax",
            )
            return resp

        if request.method == "POST":
            if not name:
                return redirect(url_for("db_select"))

            db_path = resolve_db_path(name)
            ensure_db_initialized(db_path)
            salt, phash = get_db_password_info(db_path)
            has_password = bool(salt and phash)
            can_read_without = bool(get_db_read_without_password(name))

            # A successful remembered login grants this browser session access
            # to every protected DB until the explicit /logout action.
            if _is_unlocked(name):
                touch_db_last_access(name)
                return _resp_with_db_cookie(make_response(redirect(next_url)), name)

            # Always set the selection cookie.
            pwd = (request.form.get("password") or "").strip()
            remember = bool(request.form.get("remember"))

            if not has_password:
                touch_db_last_access(name)
                return _resp_with_db_cookie(make_response(redirect(next_url)), name)

            # DB has a password
            if pwd:
                if verify_db_password(db_path, pwd):
                    touch_db_last_access(name)
                    _set_unlocked(name, remember=remember)
                    return _resp_with_db_cookie(make_response(redirect(next_url)), name)
                error = "Incorrect password."
            else:
                if can_read_without:
                    touch_db_last_access(name)
                    return _resp_with_db_cookie(make_response(redirect(next_url)), name)
                error = "Password required."

        # GET (or POST with error)
        selected = name if name in dbs else _current_db_name()
        args = {
            "name": selected,
            "next": next_url,
            "db_error": error or "",
        }
        return redirect(url_for("settings_page", **args))

    @app.route("/db/export-named", methods=["POST"])
    def db_export_named():
        """Export notes from a chosen DB using the same ZIP format as /db/export."""
        name = (request.form.get("name") or "").strip()
        if not name:
            return redirect(url_for("db_manage"))
        if not name.endswith(".db"):
            name += ".db"

        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        return export_db_notes_as_zip(db_path, name)

    @app.route("/db/download-package", methods=["POST"])
    def db_download_package():
        """Download a restorable ZIP containing the raw DB and its files."""
        name = (request.form.get("name") or "").strip()
        if not name:
            return redirect(url_for("db_manage"))
        if not name.endswith(".db"):
            name += ".db"

        try:
            mem, out_name = _db_package_bytes(name)
        except FileNotFoundError:
            return redirect(url_for("db_manage", error="Database not found."))
        except Exception:
            return redirect(url_for("db_manage", error="Database package failed."))
        return send_file(mem, mimetype="application/zip", as_attachment=True, download_name=out_name)

    @app.route("/db/import-package", methods=["POST"])
    def db_import_package():
        """Import a ZIP produced by /db/download-package as a new database."""
        package = request.files.get("package")
        backup_name = Path((request.form.get("backup_name") or "").strip()).name
        backup_path = None
        if (not package or not getattr(package, "filename", "")) and backup_name:
            candidate = BACKUP_DIR / backup_name
            if not candidate.exists() or candidate.suffix.lower() != ".zip":
                return redirect(url_for("db_manage", error="Backup ZIP not found."))
            backup_path = candidate

        if (not package or not getattr(package, "filename", "")) and not backup_path:
            return redirect(url_for("db_manage", error="Choose a database ZIP to import."))

        existing = list_db_files()
        if len(existing) >= 10:
            return redirect(url_for("db_manage", error="Maximum of 10 databases allowed."))

        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        zip_source = None
        try:
            zip_source = backup_path.open("rb") if backup_path else package.stream
            with ZipFile(zip_source) as z:
                members = z.namelist()
                if not members or any(not _safe_zip_member(n) for n in members):
                    return redirect(url_for("db_manage", error="Invalid database ZIP."))

                manifest = {}
                if DB_PACKAGE_MANIFEST in members:
                    try:
                        manifest = json.loads(z.read(DB_PACKAGE_MANIFEST).decode("utf-8"))
                    except Exception:
                        manifest = {}

                db_member = (manifest.get("database_path") or "").strip() if isinstance(manifest, dict) else ""
                if db_member not in members or not db_member.lower().endswith(".db"):
                    db_candidates = [n for n in members if n.startswith("database/") and n.lower().endswith(".db")]
                    if not db_candidates:
                        db_candidates = [n for n in members if n.lower().endswith(".db")]
                    db_member = db_candidates[0] if db_candidates else ""
                if not db_member:
                    return redirect(url_for("db_manage", error="No database file found in ZIP."))

                preferred_name = ""
                if isinstance(manifest, dict):
                    preferred_name = (manifest.get("db_name") or "").strip()
                preferred_name = preferred_name or Path(db_member).name
                target_name = _unique_db_import_name(_normalize_db_name(preferred_name), existing, stamp)
                target_path = resolve_db_path(target_name)
                target_path.parent.mkdir(exist_ok=True)

                with z.open(db_member) as src, target_path.open("wb") as dest:
                    shutil.copyfileobj(src, dest)

                try:
                    ensure_db_initialized(target_path)
                except Exception:
                    target_path.unlink(missing_ok=True)
                    return redirect(url_for("db_manage", error="The ZIP did not contain a valid VortNotes database."))

                source_key = ""
                if isinstance(manifest, dict):
                    source_key = (manifest.get("upload_key") or "").strip()
                source_key = source_key or get_db_upload_key(target_path)

                source_upload_prefix = f"uploads/{source_key}/"
                has_uploads = any(n.startswith(source_upload_prefix) and not n.endswith("/") for n in members)
                if has_uploads:
                    dest_key = source_key
                    if (UPLOAD_DIR / dest_key).exists():
                        dest_key = _unique_upload_key(source_key, UPLOAD_DIR)
                    dest_upload_dir = UPLOAD_DIR / dest_key
                    dest_upload_dir.mkdir(parents=True, exist_ok=False)

                    for n in members:
                        if not n.startswith(source_upload_prefix) or n.endswith("/"):
                            continue
                        rel = Path(n).relative_to(Path("uploads") / source_key)
                        out_path = dest_upload_dir / rel
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(n) as src, out_path.open("wb") as dest:
                            shutil.copyfileobj(src, dest)

                    if dest_key != source_key:
                        conn = db_connect(target_path)
                        conn.execute("PRAGMA foreign_keys = ON;")
                        old_prefix = f"/uploads/{source_key}/"
                        new_prefix = f"/uploads/{dest_key}/"
                        conn.execute(
                            "UPDATE notes SET content_html = REPLACE(content_html, ?, ?) "
                            "WHERE content_html LIKE '%' || ? || '%'",
                            (old_prefix, new_prefix, old_prefix),
                        )
                        conn.commit()
                        conn.close()
                    set_db_upload_key(target_path, dest_key)
                else:
                    set_db_upload_key(target_path, source_key)

                appearance = manifest.get("appearance") if isinstance(manifest, dict) else None
                if isinstance(appearance, dict) and appearance:
                    set_db_appearance(target_name, appearance)

        except zipfile.BadZipFile:
            return redirect(url_for("db_manage", error="Invalid ZIP file."))
        except Exception:
            return redirect(url_for("db_manage", error="Database import failed."))
        finally:
            if backup_path and zip_source:
                try:
                    zip_source.close()
                except Exception:
                    pass

        return redirect(url_for("db_manage", error=f"Imported database: {target_name}"))

    def export_db_notes_as_zip(db_path: Path, db_name: str):
        """
        Export notes as a ZIP that mirrors the in-app view styling and includes attachments.

        Output ZIP structure:
          - index.html
          - <note files>.html
          - static/styles.css (and styles2.css if present)
          - uploads/<db_key>/*  (all files in the DB-specific uploads folder)
        """
        import html
        import io
        from zipfile import ZIP_DEFLATED, ZipFile

        conn = db_connect(db_path)
        conn.row_factory = sqlite3.Row
        title_expr = _title_select_expr(conn)

        rows = conn.execute(
            f"SELECT id, {title_expr} AS title, tag, created_at, content_html FROM notes ORDER BY id ASC"
        ).fetchall()

        # Attachments (if table exists)
        atts_by_note = {}
        if _table_exists(conn, "attachments"):
            att_rows = conn.execute(
                "SELECT note_id, original_name, stored_name, created_at, display_order "
                "FROM attachments ORDER BY note_id ASC, COALESCE(display_order, 0) ASC, id ASC"
            ).fetchall()
            for a in att_rows:
                d = dict(a)
                d["is_image"] = is_image_filename(d.get("original_name") or "")
                atts_by_note.setdefault(d["note_id"], []).append(d)

        db_key = db_upload_key(db_name)
        upload_dir = UPLOAD_DIR / db_key

        def _safe_filename(s: str) -> str:
            s = (s or "").strip()
            if not s:
                s = "note"
            s = re.sub(r"[^\w\-]+", "_", s)
            s = re.sub(r"_+", "_", s).strip("_")
            return s[:80] or "note"

        def _relativize_upload_links(html_in: str) -> str:
            # Convert absolute /uploads/<db_key>/... -> uploads/<db_key>/... for offline browsing
            if not html_in:
                return ""
            return html_in.replace(f"/uploads/{db_key}/", f"uploads/{db_key}/")

        def _note_html(n: sqlite3.Row, attachments: list) -> str:
            title = (n["title"] or "").strip() or f"Note {n['id']}"
            tag = (n["tag"] or "").strip()
            created = fmt_dt(n["created_at"]) if n["created_at"] else ""
            body_html = _relativize_upload_links(n["content_html"] or "")

            # Badges
            badges = ""
            if tag:
                parts = [t.strip() for t in tag.split(",") if t.strip()]
                if parts:
                    badges = "".join([f"<span class='badge'>{html.escape(t)}</span>" for t in parts])

            # Attachments grid
            if attachments:
                tiles = []
                for a in attachments:
                    on = a.get("original_name") or a.get("stored_name") or "attachment"
                    stored = a.get("stored_name") or ""
                    href = f"uploads/{db_key}/{stored}"
                    if a.get("is_image"):
                        thumb = f"<img src='{href}' alt='{html.escape(on)}'>"
                    else:
                        thumb = "<div class='attach-fileicon'>📎</div>"
                    tiles.append(
                        f"<a class='attach-tile attach-link' href='{href}' target='_blank' draggable='false'>"
                        f"<div class='attach-thumb' title='{html.escape(on)}'>{thumb}</div>"
                        f"<div class='attach-name'>{html.escape(on)}</div>"
                        f"</a>"
                    )
                att_html = (
                    "<div class='card'>"
                    "<h3 style='margin-top:0; text-align:center;'>Attachments</h3>"
                    "<div class='attach-icon-grid' style='margin-top:10px;'>" + "".join(tiles) + "</div></div>"
                )
            else:
                att_html = (
                    "<div class='card'>"
                    "<h3 style='margin-top:0; text-align:center;'>Attachments</h3>"
                    "<div class='muted' style='text-align:center;'>No attachments.</div>"
                    "</div>"
                )

            return f"""<!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>{html.escape(title)}</title>
      <link rel="stylesheet" href="static/styles.css">
      <link rel="stylesheet" href="static/styles2.css">
      <link href="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.snow.css" rel="stylesheet">
    </head>
    <body>
      <div class="container">
        <div class="brand">{html.escape(db_name)}</div>

        <div class="card">
          <div class="note-header">
            <div>
              <div class="note-title-line">
                <h2 class="note-title">{html.escape(title)}</h2>
                <div class="note-meta-inline">{badges}</div>
              </div>
              <div class="muted" style="margin-top:6px;">{html.escape(created)}</div>
              <div style="margin-top:10px;"><a class="btn" href="index.html">Back to export index</a></div>
            </div>
          </div>
        </div>

        <div class="card">
          <div style="max-width: 900px; margin: 0 auto;">
            <div class="note-body ql-snow"><div class="ql-editor">{body_html}</div></div>
          </div>
        </div>

        {att_html}
      </div>
    </body>
    </html>"""

        mem = io.BytesIO()
        with ZipFile(mem, "w", ZIP_DEFLATED) as z:
            # Include app CSS for matching styling
            static_dir = BASE_DIR / "static"
            for css_name in ("styles.css", "styles2.css"):
                css_path = static_dir / css_name
                if css_path.exists():
                    z.write(css_path, f"static/{css_name}")

            # Copy uploads for this DB (if any)
            if upload_dir.exists():
                for p in upload_dir.rglob("*"):
                    if p.is_file():
                        rel = p.relative_to(UPLOAD_DIR)
                        z.write(p, str(Path("uploads") / rel))

            # Build an index page
            items = []
            for r in rows:
                title = (r["title"] or "").strip() or f"Note {r['id']}"
                safe = _safe_filename(title)
                fname = f"{r['id']:04d}_{safe}.html"
                items.append((fname, title, r["created_at"] or ""))
            li = "".join(
                f"<li><a href='{html.escape(fn)}'>{html.escape(t)}</a>"
                f" <span class='muted'>({html.escape(fmt_dt(dt) if dt else '')})</span></li>"
                for fn, t, dt in items
            )
            index_html = f"""<!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>{html.escape(db_name)} export</title>
      <link rel="stylesheet" href="static/styles.css">
      <link rel="stylesheet" href="static/styles2.css">
    </head>
    <body>
      <div class="container">
        <div class="brand">{html.escape(db_name)}</div>
        <div class="card">
          <h2 style="margin-top:0;">Exported notes</h2>
          <div class="muted">Open a note below. Attachments are included in the ZIP under uploads/{html.escape(db_key)}/.</div>
          <ul style="margin-top:12px;">{li}</ul>
        </div>
      </div>
    </body>
    </html>"""
            z.writestr("index.html", index_html)

            # Write each note html
            for r in rows:
                title = (r["title"] or "").strip() or f"Note {r['id']}"
                safe = _safe_filename(title)
                nid = int(r["id"])
                html_out = _note_html(r, atts_by_note.get(nid, []))
                z.writestr(f"{nid:04d}_{safe}.html", html_out)

        mem.seek(0)
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_name = f"{db_name.replace('.db','')}_export_{stamp}.zip"
        return send_file(mem, mimetype="application/zip", as_attachment=True, download_name=out_name)

    @app.route("/db/export", methods=["POST"])
    def db_export():
        name = selected_db_name()
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        return export_db_notes_as_zip(db_path, name)
