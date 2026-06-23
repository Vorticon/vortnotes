"""Unified Settings page routes."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from flask import redirect, render_template, request, url_for

from ..deployment import direct_https_config, generate_self_signed_tls_cert, self_signed_tls_paths
from ..settings import DATA_DIR, DB_DIR, UPLOAD_DIR


def register_settings_routes(app) -> None:
    # Late imports to avoid circular imports.
    from ..home_assistant import home_assistant_config, normalize_local_base_url
    from ..webapp import (
        THEME_PRESETS,
        _current_db_name,
        _db_has_password,
        _has_db_session_access,
        _is_admin_authed,
        _is_unlocked,
        _normalize_db_name,
        admin_password_is_set,
        ensure_db_initialized,
        fmt_dt,
        get_db_appearance,
        get_db_password_info,
        get_db_read_without_password,
        get_db_upload_key,
        get_upload_limits_effective,
        list_db_files,
        load_config,
        resolve_db_path,
        save_config,
        selected_db_name,
    )

    backup_dir = DATA_DIR / "backups"

    def _backup_entries() -> list[dict]:
        entries = []
        if not backup_dir.exists():
            return entries
        for p in sorted(backup_dir.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
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

    def _disk_stats() -> dict:
        total, used, free = shutil.disk_usage(DATA_DIR)
        return {
            "path": str(DATA_DIR),
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "used_pct": round((used / total) * 100, 1) if total else 0,
        }

    def _path_usage(path) -> dict:
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
        backup_usage = _path_usage(backup_dir)
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
            "last_run": last_run,
            "last_run_display": last_run_display,
        }

    def _home_assistant_settings() -> dict:
        return home_assistant_config(load_config())

    def _https_settings() -> dict:
        effective = direct_https_config(DATA_DIR / "config" / "config.json")
        default_cert, default_key = self_signed_tls_paths(DATA_DIR)
        cert = str(effective.get("cert_file") or default_cert)
        key = str(effective.get("key_file") or default_key)
        return {
            **effective,
            "cert_file": cert,
            "key_file": key,
            "cert_exists": bool(cert and Path(cert).is_file()),
            "key_exists": bool(key and Path(key).is_file()),
            "self_signed_cert_file": str(default_cert),
            "self_signed_key_file": str(default_key),
            "self_signed_exists": default_cert.is_file() and default_key.is_file(),
        }

    def _db_select_context() -> dict:
        raw_name = (request.values.get("name") or _current_db_name() or "").strip()
        if raw_name and not raw_name.endswith(".db"):
            raw_name += ".db"
        dbs = list_db_files()
        selected = raw_name if raw_name in dbs else _current_db_name()
        db_meta = {}
        for n in dbs:
            try:
                p = resolve_db_path(n)
                ensure_db_initialized(p)
                salt, phash = get_db_password_info(p)
                db_meta[n] = {
                    "has_password": bool(salt and phash),
                    "read_without_password": bool(get_db_read_without_password(n)),
                    "is_unlocked": bool(_has_db_session_access(n)),
                    "has_db_session": bool(_has_db_session_access(n)),
                }
            except Exception:
                db_meta[n] = {
                    "has_password": False,
                    "read_without_password": False,
                    "is_unlocked": False,
                    "has_db_session": False,
                }
        sel_meta = db_meta.get(
            selected,
            {
                "has_password": False,
                "read_without_password": False,
                "is_unlocked": False,
                "has_db_session": False,
            },
        )
        return {
            "select_dbs": dbs,
            "select_selected_db": selected,
            "select_next_url": request.values.get("next") or url_for("index"),
            "select_error": (request.values.get("db_error") or "").strip(),
            "select_db_meta_json": json.dumps(db_meta),
            "selected_has_password": bool(sel_meta.get("has_password")),
            "selected_read_without": bool(sel_meta.get("read_without_password")),
            "selected_is_unlocked": bool(sel_meta.get("is_unlocked")),
            "selected_has_db_session": bool(sel_meta.get("has_db_session")),
        }

    def _db_admin_context() -> dict:
        cfg = load_config()
        default_db = _normalize_db_name(cfg.get("default_db") or "notes.db")
        dbs = list_db_files()
        password_set = {}
        for n in dbs:
            p = resolve_db_path(n)
            ensure_db_initialized(p)
            password_set[n] = _db_has_password(p)

        theme_map = {}
        for n in dbs:
            a = get_db_appearance(n) or {}
            preset_raw = (a.get("preset") or "").strip().lower()
            accent = (a.get("accent") or "").strip()
            accent2 = (a.get("accent2") or "").strip()
            mode = "default"
            preset_display = preset_raw or "default"
            if preset_raw:
                if preset_raw in THEME_PRESETS:
                    p1, p2 = THEME_PRESETS[preset_raw]
                    accent = accent or p1
                    accent2 = accent2 or p2
                    mode = "preset" if preset_raw != "default" else "default"
                else:
                    preset_display = "default"
            else:
                mode = "custom" if (accent or accent2) else "default"
            try:
                btn_hover_outline = int(a.get("btn_hover_outline") or 0)
            except Exception:
                btn_hover_outline = 0
            try:
                bg_brightness = int(a.get("bg_brightness") or 10)
            except Exception:
                bg_brightness = 10
            bg_filename = (a.get("bg_filename") or "").strip()
            theme_map[n] = {
                "mode": mode,
                "preset": preset_display,
                "accent": accent or "",
                "accent2": accent2 or "",
                "has_bg": bool(bg_filename),
                "bg_filename": bg_filename,
                "bg_brightness": max(0, min(100, bg_brightness)),
                "btn_hover_outline": 1 if btn_hover_outline else 0,
            }

        last_access_map = {}
        perm_read_map = {}
        for n in dbs:
            a = get_db_appearance(n) or {}
            last_access_map[n] = fmt_dt(str(a.get("last_access") or ""))
            perm_read_map[n] = 1 if get_db_read_without_password(n) else 0

        is_set = admin_password_is_set()
        return {
            "dbs": dbs,
            "default_db": default_db,
            "selected_db": selected_db_name(),
            "last_access_map": last_access_map,
            "perm_read_map": perm_read_map,
            "password_set": password_set,
            "theme_presets": ["default"] + sorted(THEME_PRESETS.keys()),
            "theme_map": theme_map,
            "admin_password_set": is_set,
            "admin_password_is_set": is_set,
            "error_msg": (request.args.get("error") or "").strip(),
            "upload_cfg": get_upload_limits_effective(),
            "backup_entries": _backup_entries(),
            "auto_backup": _auto_backup_config(),
            "disk_stats": _disk_stats(),
            "storage_stats": _storage_stats(),
            "home_assistant": _home_assistant_settings(),
            "https_cfg": _https_settings(),
            "embedded_settings": True,
        }

    @app.route("/settings/home-assistant", methods=["POST"], endpoint="settings_home_assistant")
    def settings_home_assistant():
        is_admin_set = admin_password_is_set()
        if not is_admin_set:
            return redirect(url_for("db_admin_login", next=url_for("settings_page")))
        if not _is_admin_authed():
            return redirect(url_for("db_admin_login", next=url_for("settings_page")))

        cfg = load_config()
        current = cfg.get("home_assistant")
        if not isinstance(current, dict):
            current = {}

        enabled = (request.form.get("enabled") or "").strip() == "1"
        raw_url = (request.form.get("base_url") or "").strip()
        base_url = normalize_local_base_url(raw_url)
        if raw_url and not base_url:
            return redirect(url_for("settings_page", error="Home Assistant URL must be local."))

        token = (request.form.get("token") or "").strip()
        if not token:
            token = str(current.get("token") or "").strip()

        cfg["home_assistant"] = {
            "enabled": enabled,
            "base_url": base_url,
            "token": token,
        }
        save_config(cfg)
        return redirect(url_for("settings_page"))

    @app.route("/settings/https", methods=["POST"], endpoint="settings_https")
    def settings_https():
        if not admin_password_is_set() or not _is_admin_authed():
            return redirect(url_for("db_admin_login", next=url_for("settings_page")))
        if os.getenv("VORTNOTES_TLS_CERT_FILE", "").strip() or os.getenv("VORTNOTES_TLS_KEY_FILE", "").strip():
            return redirect(url_for("settings_page", error="HTTPS is controlled by environment variables."))

        enabled = (request.form.get("enabled") or "").strip().lower() in {"1", "true", "on", "yes"}
        cert_file = (request.form.get("cert_file") or "").strip()
        key_file = (request.form.get("key_file") or "").strip()
        if enabled:
            for label, value in (("certificate", cert_file), ("private key", key_file)):
                path = Path(value)
                if not value or not path.is_absolute() or not path.is_file() or not os.access(path, os.R_OK):
                    return redirect(
                        url_for(
                            "settings_page",
                            error=f"HTTPS {label} is not readable inside the container: {value or '(empty)'}",
                        )
                    )

        cfg = load_config()
        cfg["https"] = {"enabled": enabled, "cert_file": cert_file, "key_file": key_file}
        save_config(cfg)
        return redirect(url_for("settings_page", notice="HTTPS configuration saved. Restart VortNotes to apply it."))

    @app.route("/settings/https/self-signed", methods=["POST"], endpoint="settings_https_self_signed")
    def settings_https_self_signed():
        if not admin_password_is_set() or not _is_admin_authed():
            return redirect(url_for("db_admin_login", next=url_for("settings_page")))
        if os.getenv("VORTNOTES_TLS_CERT_FILE", "").strip() or os.getenv("VORTNOTES_TLS_KEY_FILE", "").strip():
            return redirect(url_for("settings_page", error="HTTPS is controlled by environment variables."))

        try:
            cert_path, key_path = generate_self_signed_tls_cert(DATA_DIR, overwrite=True)
        except Exception:
            return redirect(url_for("settings_page", error="Self-signed certificate generation failed."))

        cfg = load_config()
        cfg["https"] = {"enabled": True, "cert_file": str(cert_path), "key_file": str(key_path)}
        save_config(cfg)
        return redirect(
            url_for(
                "settings_page",
                notice="Self-signed HTTPS certificate generated. Restart VortNotes, then reconnect with https://.",
            )
        )

    @app.route("/settings")
    def settings_page():
        name = (_current_db_name() or "").strip()
        db_locked = None
        try:
            if name:
                db_path = resolve_db_path(name)
                ensure_db_initialized(db_path)
                salt, phash = get_db_password_info(db_path)
                if salt and phash:
                    db_locked = not _is_unlocked(name)
        except Exception:
            db_locked = None

        is_admin_set = admin_password_is_set()
        can_admin = (not is_admin_set) or _is_admin_authed()
        admin_setup_required = not is_admin_set
        ctx = {
            "settings_selected_db": name,
            "db_locked": db_locked,
            "is_logged_in": bool(_has_db_session_access(name)),
            "can_admin": can_admin and not admin_setup_required,
            "admin_setup_required": admin_setup_required,
            "admin_login_required": is_admin_set and not can_admin,
            "admin_login_url": url_for("db_admin_login", next=url_for("settings_page")),
            "notice_msg": (request.args.get("notice") or "").strip(),
        }
        ctx.update(_db_select_context())
        if can_admin and not admin_setup_required:
            ctx.update(_db_admin_context())

        return render_template("settings.html", **ctx)
