"""Content routes.

The Content page unifies links, groups, and uploaded files.
Items are stored per selected database and rendered as an icon grid.
"""

from __future__ import annotations

import ipaddress
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from flask import Response, abort, flash, jsonify, redirect, render_template, request, send_from_directory, url_for


def register_content_routes(app) -> None:
    # Late imports to avoid cycles.
    from ..home_assistant import HomeAssistantError, call_home_assistant
    from ..settings import DATA_DIR

    CONTENT_APPS = {
        "tetris": {"title": "Falling Blocks", "description": "Stack falling shapes and clear complete rows"},
        "jewels": {"title": "Jewel Match", "description": "Swap jewels and match three"},
        "memory": {"title": "Memory Match", "description": "Flip tiles and find every matching pair"},
        "minesweeper": {"title": "Minesweeper", "description": "Clear the field without touching a mine"},
        "breakout": {"title": "Breakout", "description": "Bounce the ball and clear every brick"},
        "simon": {"title": "Sequence Recall", "description": "Watch the sequence and repeat it from memory"},
        "snake": {"title": "Snake", "description": "Eat apples, grow longer, and avoid your own tail"},
        "kanban": {"title": "Kanban Board", "description": "Track tasks and link cards to notes"},
        "calendar": {"title": "Calendar Lite", "description": "A simple local calendar for quick planning"},
        "sticky": {"title": "Sticky Notes", "description": "Quick colorful notes that autosave"},
        "ambient": {"title": "Ambient Focus", "description": "Focus timer with generated ambient soundscapes"},
    }
    from ..webapp import (
        _attachment_ext_allowed,
        _current_db_name,
        _is_admin_authed,
        _is_unlocked,
        _title_select_expr,
        current_upload_dir,
        db_guest_can,
        ensure_db_initialized,
        get_attachment_max_bytes,
        get_db,
        get_db_password_info,
        iso_now,
        load_config,
        resolve_db_path,
        touch_db_last_access,
        unique_store_name,
    )

    CAMERA_HLS_DIR = DATA_DIR / "camera_hls"
    CAMERA_HLS_DIR.mkdir(parents=True, exist_ok=True)
    _camera_streams: dict[int, dict] = {}
    _camera_streams_lock = threading.Lock()

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

        wants_app = (next_url or "").startswith("/content/apps/")
        allowed = db_guest_can(name, "apps", "use") if wants_app else db_guest_can(name, "content", "read")
        if salt and phash and not _is_unlocked(name) and not allowed:
            # For AJAX/JSON fetch requests, return a JSON error instead of HTML redirect.
            if _wants_json():
                return jsonify({"ok": False, "error": "auth_required", "next": next_url}), 401
            return redirect(url_for("settings_page", name=name, next=next_url))
        return None

    @app.route("/content/apps/<app_id>", methods=["GET"], endpoint="content_app")
    def content_app(app_id: str):
        gate = _require_read_access(request.path)
        if gate:
            return gate
        app_id = (app_id or "").strip().lower()
        app_info = CONTENT_APPS.get(app_id)
        if not app_info:
            abort(404)
        if app_id == "sticky":
            db = get_db()
            notes = [
                dict(row)
                for row in db.execute(
                    "SELECT id, title, body, color, created_at, updated_at "
                    "FROM sticky_notes ORDER BY updated_at DESC, id DESC"
                ).fetchall()
            ]
            name = _current_db_name()
            salt, phash = get_db_password_info(resolve_db_path(name))
            can_edit = not (salt and phash) or _is_unlocked(name)
            return render_template("sticky_notes_app.html", notes=notes, can_edit=can_edit)
        if app_id == "ambient":
            return render_template("ambient_focus_app.html")
        if app_id == "kanban":
            db = get_db()
            now = iso_now()
            count = db.execute("SELECT COUNT(1) AS c FROM kanban_columns").fetchone()["c"]
            if not count:
                for order, title in enumerate(("Backlog", "Doing", "Done")):
                    db.execute(
                        "INSERT INTO kanban_columns (title, display_order, created_at, updated_at) VALUES (?,?,?,?)",
                        (title, order, now, now),
                    )
                db.commit()
            columns = [
                dict(row)
                for row in db.execute(
                    "SELECT id, title, display_order FROM kanban_columns ORDER BY display_order ASC, id ASC"
                ).fetchall()
            ]
            cards = [
                dict(row)
                for row in db.execute(
                    "SELECT id, column_id, title, body, note_id, display_order "
                    "FROM kanban_cards ORDER BY column_id ASC, display_order ASC, id ASC"
                ).fetchall()
            ]
            title_expr = _title_select_expr(db)
            notes = [
                dict(row)
                for row in db.execute(
                    f"SELECT id, {title_expr} AS title, tag FROM notes ORDER BY updated_at DESC, id DESC LIMIT 500"
                ).fetchall()
            ]
            name = _current_db_name()
            salt, phash = get_db_password_info(resolve_db_path(name))
            can_edit = not (salt and phash) or _is_unlocked(name)
            return render_template(
                "kanban_app.html",
                columns=columns,
                cards=cards,
                notes=notes,
                can_edit=can_edit,
            )
        if app_id == "calendar":
            return render_template("calendar_lite_app.html")
        return render_template(
            "content_app.html",
            app_id=app_id,
            app_info=app_info,
            can_clear_scores=_is_admin_authed(),
        )

    def _require_write_access(next_url: str):
        name = _current_db_name()
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        touch_db_last_access(name)
        salt, phash = get_db_password_info(db_path)
        if salt and phash and not _is_unlocked(name) and not db_guest_can(name, "content", "manage"):
            return redirect(url_for("settings_page", name=name, next=next_url))
        return None

    def _json_gate(gate):
        if not gate:
            return None
        return jsonify({"ok": False, "error": "auth_required"}), 401

    def _plain_auth_gate(gate):
        if not gate:
            return None
        return (
            "Camera stream access requires unlocking this database.",
            401,
            {"Content-Type": "text/plain; charset=utf-8"},
        )

    def _is_local_camera_host(host: str) -> bool:
        host = (host or "").strip().lower()
        if not host:
            return False
        if host in {"localhost"} or host.endswith(".local"):
            return True
        try:
            ip = ipaddress.ip_address(host)
            return bool(ip.is_private or ip.is_loopback or ip.is_link_local)
        except ValueError:
            return "." not in host

    def _normalize_camera_base(raw: str) -> str:
        raw = (raw or "").strip().rstrip("/")
        if not raw:
            return ""
        if "://" not in raw:
            raw = "http://" + raw
        try:
            parsed = urlparse(raw)
        except Exception:
            return ""
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        if not _is_local_camera_host(parsed.hostname or ""):
            return ""
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))

    def _normalize_camera_rtsp(raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlparse(raw)
        except Exception:
            return ""
        if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.netloc:
            return ""
        if not _is_local_camera_host(parsed.hostname or ""):
            return ""
        return raw

    def _normalize_camera_vendor(raw: str) -> str:
        value = (raw or "generic").strip().lower()
        return value if value in {"generic", "reolink"} else "generic"

    def _normalize_camera_playback(raw: str, vendor: str) -> str:
        value = (raw or "snapshot").strip().lower()
        if value not in {"snapshot", "rtsp"}:
            value = "snapshot"
        if value == "snapshot" and vendor != "reolink":
            value = "rtsp"
        return value

    def _normalize_camera_ptz(raw: str, vendor: str) -> str:
        value = (raw or "none").strip().lower()
        if vendor != "reolink":
            return "none"
        return value if value in {"none", "reolink"} else "none"

    def _camera_profiles() -> list[dict]:
        cfg = load_config()
        profiles = cfg.get("cameras")
        if not isinstance(profiles, list):
            return []
        out = []
        for item in profiles:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            base_url = _normalize_camera_base(str(item.get("base_url") or ""))
            vendor = _normalize_camera_vendor(str(item.get("vendor") or item.get("kind") or "reolink"))
            playback_mode = _normalize_camera_playback(str(item.get("playback_mode") or "snapshot"), vendor)
            stream = _normalize_camera_rtsp(str(item.get("stream") or ""))
            stream_quality = str(item.get("stream_quality") or "main").strip().lower()
            if stream_quality not in {"main", "sub"}:
                stream_quality = "main"
            try:
                channel = max(0, int(item.get("channel") or 0))
            except Exception:
                channel = 0
            stream_index = channel + 1
            if vendor == "reolink" and playback_mode == "rtsp" and not stream:
                parsed = urlparse(base_url)
                host = parsed.hostname or ""
                if host:
                    auth = ""
                    username = str(item.get("username") or "").strip()
                    password = str(item.get("password") or "")
                    if username:
                        auth = quote(username, safe="")
                        if password:
                            auth += ":" + quote(password, safe="")
                        auth += "@"
                    stream = _normalize_camera_rtsp(
                        f"rtsp://{auth}{host}:554/h264Preview_{stream_index:02d}_{stream_quality}"
                    )
            if (
                not pid
                or not title
                or (playback_mode == "snapshot" and not base_url)
                or (playback_mode == "rtsp" and not stream)
            ):
                continue
            try:
                refresh_ms = max(100, min(10000, int(item.get("refresh_ms") or 500)))
            except Exception:
                refresh_ms = 500
            ptz_mode = _normalize_camera_ptz(str(item.get("ptz_mode") or "none"), vendor)
            out.append(
                {
                    "id": pid,
                    "title": title,
                    "kind": vendor,
                    "vendor": vendor,
                    "playback_mode": playback_mode,
                    "base_url": base_url,
                    "username": str(item.get("username") or "").strip(),
                    "password": str(item.get("password") or ""),
                    "channel": channel,
                    "stream": stream,
                    "stream_quality": stream_quality,
                    "refresh_ms": refresh_ms,
                    "ptz_mode": ptz_mode,
                }
            )
        return out

    def _camera_profile_by_id(profile_id: str) -> dict:
        profile_id = str(profile_id or "").strip()
        if not profile_id:
            return {}
        for profile in _camera_profiles():
            if profile.get("id") == profile_id:
                return profile
        return {}

    def _camera_config_from_form(form) -> dict:
        profile_id = (form.get("camera_profile_id") or "").strip()
        profile = _camera_profile_by_id(profile_id)
        return {"profile_id": profile_id} if profile else {}

    def _camera_config_from_row(row) -> dict:
        if not row or (row["item_kind"] or "") != "camera":
            return {}
        try:
            data = json.loads(row["url"] or "{}")
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        profile_id = str(data.get("profile_id") or "").strip()
        profile = _camera_profile_by_id(profile_id)
        if profile:
            merged = dict(profile)
            merged["profile_id"] = profile_id
            return merged
        vendor = _normalize_camera_vendor(str(data.get("vendor") or data.get("kind") or "generic"))
        data["vendor"] = vendor
        data["kind"] = vendor
        data["playback_mode"] = _normalize_camera_playback(str(data.get("playback_mode") or "snapshot"), vendor)
        data["base_url"] = _normalize_camera_base(str(data.get("base_url") or ""))
        data["stream"] = _normalize_camera_rtsp(str(data.get("stream") or ""))
        try:
            data["channel"] = max(0, int(data.get("channel") or 0))
        except Exception:
            data["channel"] = 0
        try:
            data["refresh_ms"] = max(100, min(10000, int(data.get("refresh_ms") or 500)))
        except Exception:
            data["refresh_ms"] = 500
        data["ptz_mode"] = _normalize_camera_ptz(str(data.get("ptz_mode") or "none"), vendor)
        return data

    def _camera_public_config(row) -> dict:
        cfg = _camera_config_from_row(row)
        return {
            "profile_id": cfg.get("profile_id") or "",
            "profile_title": cfg.get("title") or "",
            "vendor": cfg.get("vendor") or cfg.get("kind") or "generic",
            "playback_mode": cfg.get("playback_mode") or "snapshot",
            "base_url": cfg.get("base_url") or "",
            "channel": int(cfg.get("channel") or 0),
            "refresh_ms": int(cfg.get("refresh_ms") or 500),
            "ptz_mode": cfg.get("ptz_mode") or "none",
            "stream": cfg.get("stream") or "",
            "has_credentials": bool(cfg.get("username") or cfg.get("password")),
        }

    def _camera_editor_config(row) -> dict:
        cfg = _camera_public_config(row)
        full = _camera_config_from_row(row)
        cfg["username"] = full.get("username") or ""
        cfg["stream"] = full.get("stream") or ""
        return cfg

    def _camera_reolink_url(
        cfg: dict,
        command: str,
        extra: dict | None = None,
        *,
        token: str | None = None,
        include_credentials: bool = True,
    ) -> str:
        base = cfg.get("base_url") or ""
        if not base:
            return ""
        params = {
            "cmd": command,
            "channel": str(int(cfg.get("channel") or 0)),
            "rs": "vortnotes",
        }
        if token:
            params["token"] = token
        elif include_credentials and cfg.get("username"):
            params["user"] = cfg.get("username")
        if include_credentials and not token and cfg.get("password"):
            params["password"] = cfg.get("password")
        if extra:
            params.update({k: str(v) for k, v in extra.items() if v is not None})
        return base + "/cgi-bin/api.cgi?" + urlencode(params)

    def _camera_reolink_login_token(cfg: dict) -> str:
        base = cfg.get("base_url") or ""
        username = cfg.get("username") or ""
        password = cfg.get("password") or ""
        if not base or not username or not password:
            return ""
        url = base + "/cgi-bin/api.cgi?cmd=Login"
        payload = json.dumps(
            [
                {
                    "cmd": "Login",
                    "param": {
                        "User": {
                            "userName": username,
                            "password": password,
                        }
                    },
                }
            ]
        ).encode("utf-8")
        req = Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Vortnotes/Camera",
            },
            method="POST",
        )
        with urlopen(req, timeout=8) as resp:
            data = resp.read(1024 * 1024)
        try:
            body = json.loads(data.decode("utf-8"))
        except Exception:
            return ""
        if not isinstance(body, list) or not body:
            return ""
        first = body[0] if isinstance(body[0], dict) else {}
        if int(first.get("code", -1)) != 0:
            return ""
        value = first.get("value") if isinstance(first.get("value"), dict) else {}
        token = value.get("Token") if isinstance(value.get("Token"), dict) else {}
        return str(token.get("name") or "").strip()

    def _camera_reolink_logout_token(cfg: dict, token: str) -> None:
        token = (token or "").strip()
        base = cfg.get("base_url") or ""
        if not base or not token:
            return
        try:
            logout_url = _camera_reolink_url(cfg, "Logout", token=token, include_credentials=False)
            req = Request(logout_url, headers={"User-Agent": "Vortnotes/Camera"})
            with urlopen(req, timeout=4) as resp:
                resp.read(256 * 1024)
        except Exception:
            pass

    def _looks_like_image(data: bytes) -> bool:
        if data.startswith(b"\xff\xd8\xff"):
            return data.rstrip().endswith(b"\xff\xd9")
        return data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"GIF87a") or data.startswith(b"GIF89a")

    def _read_camera_snapshot(resp, max_bytes: int = 64 * 1024 * 1024) -> bytes:
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Camera snapshot is larger than 64 MB.")
        return data

    def _short_camera_body(data: bytes) -> str:
        text = data[:800].decode("utf-8", "replace")
        return " ".join(text.split())

    def _camera_reolink_error(data: bytes) -> str:
        try:
            body = json.loads(data[: 1024 * 1024].decode("utf-8", "replace"))
        except Exception:
            return ""
        items = body if isinstance(body, list) else [body]
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                code = int(item.get("code", 0))
            except Exception:
                code = 0
            if code == 0:
                continue
            err = item.get("error") if isinstance(item.get("error"), dict) else {}
            detail = str(err.get("detail") or "").strip()
            rsp = str(err.get("rspCode") or "").strip()
            if detail and rsp:
                return f"{detail} ({rsp})"
            return detail or _short_camera_body(data) or "Camera returned an error."
        return ""

    def _camera_response_is_login_failed(data: bytes) -> bool:
        text = data[:1200].decode("utf-8", "replace").lower()
        return "login failed" in text or "rspcode" in text and "-7" in text

    def _camera_reolink_json_command(cfg: dict, command: str, param: dict, token: str) -> bytes:
        base = cfg.get("base_url") or ""
        if not base or not token:
            return b""
        url = base + "/cgi-bin/api.cgi?" + urlencode({"cmd": command, "token": token})
        payload = json.dumps([{"cmd": command, "action": 0, "param": param}]).encode("utf-8")
        req = Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Vortnotes/Camera",
            },
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            return resp.read(512 * 1024)

    def _camera_hls_dir(item_id: int) -> Path:
        return CAMERA_HLS_DIR / str(int(item_id))

    def _camera_stop_stream_locked(item_id: int) -> None:
        entry = _camera_streams.pop(int(item_id), None)
        proc = entry.get("proc") if isinstance(entry, dict) else None
        if proc and getattr(proc, "poll", lambda: None)() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass

    def _camera_prepare_hls_dir(out_dir: Path) -> tuple[bool, str]:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return False, f"Unable to create HLS output directory: {exc}"
        patterns = ("stream.m3u8", "stream.m3u8.tmp", "ffmpeg.log", "seg_*.ts")
        for _attempt in range(3):
            failures: list[str] = []
            for pattern in patterns:
                for path in out_dir.glob(pattern):
                    try:
                        if path.is_file() or path.is_symlink():
                            path.unlink()
                    except FileNotFoundError:
                        pass
                    except Exception as exc:
                        failures.append(f"{path.name}: {exc}")
            if not failures:
                return True, ""
            time.sleep(0.15)
        return True, ""

    def _camera_cleanup_stale_streams(max_idle: int = 90) -> None:
        now = time.time()
        with _camera_streams_lock:
            stale = [
                item_id
                for item_id, entry in _camera_streams.items()
                if (not entry.get("proc"))
                or entry["proc"].poll() is not None
                or now - float(entry.get("last_seen") or 0) > max_idle
            ]
            for item_id in stale:
                _camera_stop_stream_locked(item_id)

    def _camera_start_hls_stream(item_id: int, cfg: dict) -> tuple[bool, str]:
        stream = _normalize_camera_rtsp(str(cfg.get("stream") or ""))
        if not stream:
            return False, "RTSP stream URL is missing or not local."
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False, "FFmpeg is not installed in this runtime."
        out_dir = _camera_hls_dir(item_id)
        _camera_cleanup_stale_streams()
        with _camera_streams_lock:
            existing = _camera_streams.get(int(item_id))
            proc = existing.get("proc") if existing else None
            if proc and proc.poll() is None:
                existing["last_seen"] = time.time()
                return True, ""
            _camera_stop_stream_locked(item_id)
            ok, error = _camera_prepare_hls_dir(out_dir)
            if not ok:
                return False, f"Unable to prepare HLS output: {error}"
            playlist = out_dir / "stream.m3u8"
            segment = out_dir / "seg_%05d.ts"
            ffmpeg_log = out_dir / "ffmpeg.log"
            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "info",
                "-rtsp_transport",
                "tcp",
                "-i",
                stream,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-tune",
                "zerolatency",
                "-profile:v",
                "baseline",
                "-level",
                "3.1",
                "-pix_fmt",
                "yuv420p",
                "-g",
                "30",
                "-keyint_min",
                "30",
                "-sc_threshold",
                "0",
                "-f",
                "hls",
                "-hls_time",
                "1",
                "-hls_list_size",
                "4",
                "-hls_flags",
                "delete_segments+omit_endlist+independent_segments",
                "-hls_segment_filename",
                str(segment),
                str(playlist),
            ]
            try:
                log_handle = ffmpeg_log.open("ab")
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=log_handle,
                    cwd=str(out_dir),
                )
                try:
                    log_handle.close()
                except Exception:
                    pass
            except Exception as exc:
                return False, f"Unable to start FFmpeg: {exc}"
            _camera_streams[int(item_id)] = {"proc": proc, "last_seen": time.time()}
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if playlist.is_file() and playlist.stat().st_size > 0:
                return True, ""
            if proc.poll() is not None:
                try:
                    detail = ffmpeg_log.read_text(encoding="utf-8", errors="replace")[-600:].strip()
                except Exception:
                    detail = ""
                return False, "FFmpeg stopped before the stream was ready." + (f" {detail}" if detail else "")
            time.sleep(0.2)
        return False, "Timed out waiting for FFmpeg to create the stream playlist."

    @app.route("/content/apps/sticky/save", methods=["POST"], endpoint="sticky_note_save")
    def sticky_note_save():
        gate = _json_gate(_require_write_access(url_for("content_app", app_id="sticky")))
        if gate:
            return gate
        payload = request.get_json(silent=True) or request.form
        note_id_raw = str(payload.get("id") or "").strip()
        note_id = int(note_id_raw) if note_id_raw.isdigit() else None
        title = str(payload.get("title") or "").strip()[:120]
        body = str(payload.get("body") or "")[:10000]
        color = str(payload.get("color") or "yellow").strip().lower()
        if color not in {"yellow", "pink", "blue", "green", "purple", "orange"}:
            color = "yellow"
        now = iso_now()
        db = get_db()
        if note_id is not None:
            row = db.execute("SELECT id FROM sticky_notes WHERE id=?", (note_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "not_found"}), 404
            db.execute(
                "UPDATE sticky_notes SET title=?, body=?, color=?, updated_at=? WHERE id=?",
                (title, body, color, now, note_id),
            )
        else:
            cur = db.execute(
                "INSERT INTO sticky_notes (title, body, color, created_at, updated_at) VALUES (?,?,?,?,?)",
                (title, body, color, now, now),
            )
            note_id = int(cur.lastrowid)
        db.commit()
        return jsonify(
            {"ok": True, "note": {"id": note_id, "title": title, "body": body, "color": color, "updated_at": now}}
        )

    @app.route("/content/apps/sticky/delete", methods=["POST"], endpoint="sticky_note_delete")
    def sticky_note_delete():
        gate = _json_gate(_require_write_access(url_for("content_app", app_id="sticky")))
        if gate:
            return gate
        payload = request.get_json(silent=True) or request.form
        note_id_raw = str(payload.get("id") or "").strip()
        if not note_id_raw.isdigit():
            return jsonify({"ok": False, "error": "invalid_id"}), 400
        db = get_db()
        db.execute("DELETE FROM sticky_notes WHERE id=?", (int(note_id_raw),))
        db.commit()
        return jsonify({"ok": True})

    def _kanban_payload():
        return request.get_json(silent=True) or request.form

    def _kanban_int(value, default=None):
        raw = str(value if value is not None else "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    @app.route("/content/apps/kanban/column/save", methods=["POST"], endpoint="kanban_column_save")
    def kanban_column_save():
        gate = _json_gate(_require_write_access(url_for("content_app", app_id="kanban")))
        if gate:
            return gate
        payload = _kanban_payload()
        column_id = _kanban_int(payload.get("id"))
        title = str(payload.get("title") or "").strip()[:80]
        if not title:
            return jsonify({"ok": False, "error": "title_required"}), 400
        db = get_db()
        now = iso_now()
        if column_id:
            row = db.execute("SELECT id FROM kanban_columns WHERE id=?", (column_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "not_found"}), 404
            db.execute("UPDATE kanban_columns SET title=?, updated_at=? WHERE id=?", (title, now, column_id))
        else:
            order_row = db.execute("SELECT COALESCE(MAX(display_order), -1) AS m FROM kanban_columns").fetchone()
            order_num = int(order_row["m"] if order_row and order_row["m"] is not None else -1) + 1
            cur = db.execute(
                "INSERT INTO kanban_columns (title, display_order, created_at, updated_at) VALUES (?,?,?,?)",
                (title, order_num, now, now),
            )
            column_id = int(cur.lastrowid)
        db.commit()
        return jsonify({"ok": True, "column": {"id": column_id, "title": title}})

    @app.route("/content/apps/kanban/column/delete", methods=["POST"], endpoint="kanban_column_delete")
    def kanban_column_delete():
        gate = _json_gate(_require_write_access(url_for("content_app", app_id="kanban")))
        if gate:
            return gate
        payload = _kanban_payload()
        column_id = _kanban_int(payload.get("id"))
        if not column_id:
            return jsonify({"ok": False, "error": "invalid_id"}), 400
        db = get_db()
        remaining = db.execute("SELECT COUNT(1) AS c FROM kanban_columns WHERE id<>?", (column_id,)).fetchone()["c"]
        if int(remaining or 0) < 1:
            return jsonify({"ok": False, "error": "last_column"}), 400
        db.execute("DELETE FROM kanban_columns WHERE id=?", (column_id,))
        db.commit()
        return jsonify({"ok": True})

    @app.route("/content/apps/kanban/card/save", methods=["POST"], endpoint="kanban_card_save")
    def kanban_card_save():
        gate = _json_gate(_require_write_access(url_for("content_app", app_id="kanban")))
        if gate:
            return gate
        payload = _kanban_payload()
        card_id = _kanban_int(payload.get("id"))
        column_id = _kanban_int(payload.get("column_id"))
        title = str(payload.get("title") or "").strip()[:140]
        body = str(payload.get("body") or "")[:5000]
        note_id = _kanban_int(payload.get("note_id"))
        if not column_id or not title:
            return jsonify({"ok": False, "error": "column_and_title_required"}), 400
        db = get_db()
        if not db.execute("SELECT id FROM kanban_columns WHERE id=?", (column_id,)).fetchone():
            return jsonify({"ok": False, "error": "column_not_found"}), 404
        if note_id and not db.execute("SELECT id FROM notes WHERE id=?", (note_id,)).fetchone():
            note_id = None
        now = iso_now()
        if card_id:
            row = db.execute("SELECT id FROM kanban_cards WHERE id=?", (card_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "not_found"}), 404
            db.execute(
                "UPDATE kanban_cards SET column_id=?, title=?, body=?, note_id=?, updated_at=? WHERE id=?",
                (column_id, title, body, note_id, now, card_id),
            )
        else:
            order_row = db.execute(
                "SELECT COALESCE(MAX(display_order), -1) AS m FROM kanban_cards WHERE column_id=?", (column_id,)
            ).fetchone()
            order_num = int(order_row["m"] if order_row and order_row["m"] is not None else -1) + 1
            cur = db.execute(
                "INSERT INTO kanban_cards (column_id, title, body, note_id, display_order, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (column_id, title, body, note_id, order_num, now, now),
            )
            card_id = int(cur.lastrowid)
        db.commit()
        return jsonify({"ok": True, "card": {"id": card_id, "column_id": column_id, "title": title, "body": body, "note_id": note_id}})

    @app.route("/content/apps/kanban/card/move", methods=["POST"], endpoint="kanban_card_move")
    def kanban_card_move():
        gate = _json_gate(_require_write_access(url_for("content_app", app_id="kanban")))
        if gate:
            return gate
        payload = _kanban_payload()
        card_id = _kanban_int(payload.get("id"))
        column_id = _kanban_int(payload.get("column_id"))
        index = max(0, _kanban_int(payload.get("index"), 0) or 0)
        if not card_id or not column_id:
            return jsonify({"ok": False, "error": "invalid_request"}), 400
        db = get_db()
        if not db.execute("SELECT id FROM kanban_cards WHERE id=?", (card_id,)).fetchone():
            return jsonify({"ok": False, "error": "not_found"}), 404
        if not db.execute("SELECT id FROM kanban_columns WHERE id=?", (column_id,)).fetchone():
            return jsonify({"ok": False, "error": "column_not_found"}), 404
        card_ids = [
            int(row["id"])
            for row in db.execute(
                "SELECT id FROM kanban_cards WHERE column_id=? AND id<>? ORDER BY display_order ASC, id ASC",
                (column_id, card_id),
            ).fetchall()
        ]
        card_ids.insert(min(index, len(card_ids)), card_id)
        now = iso_now()
        db.execute("UPDATE kanban_cards SET column_id=?, updated_at=? WHERE id=?", (column_id, now, card_id))
        for order, cid in enumerate(card_ids):
            db.execute("UPDATE kanban_cards SET display_order=? WHERE id=?", (order, cid))
        db.commit()
        return jsonify({"ok": True})

    @app.route("/content/apps/kanban/card/delete", methods=["POST"], endpoint="kanban_card_delete")
    def kanban_card_delete():
        gate = _json_gate(_require_write_access(url_for("content_app", app_id="kanban")))
        if gate:
            return gate
        payload = _kanban_payload()
        card_id = _kanban_int(payload.get("id"))
        if not card_id:
            return jsonify({"ok": False, "error": "invalid_id"}), 400
        db = get_db()
        db.execute("DELETE FROM kanban_cards WHERE id=?", (card_id,))
        db.commit()
        return jsonify({"ok": True})

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

    ICON_LIBRARY_DIR = "content_icons"
    LEGACY_ICON_DIRS = ("link_icons", "link_group_icons")

    def _save_icon_upload(file_storage) -> str:
        if not file_storage or not getattr(file_storage, "filename", ""):
            return ""
        if not _attachment_ext_allowed(file_storage.filename):
            return ""
        icon_dir = current_upload_dir() / ICON_LIBRARY_DIR
        icon_dir.mkdir(parents=True, exist_ok=True)
        # unique_store_name expects (target_dir, original_filename)
        # Read up to the configured max (reuse attachment max).
        max_bytes = int(get_attachment_max_bytes())
        data = file_storage.stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            return ""

        # Enforce icon dimensions by normalizing everything to a square PNG.
        # This keeps the Content grid + editor consistent even when users upload huge images.
        try:
            from io import BytesIO

            from PIL import Image

            ICON_PX = 256
            im = Image.open(BytesIO(data))
            im.load()

            # Convert to RGBA so we can preserve transparency if present.
            if im.mode not in ("RGBA", "LA"):
                im = im.convert("RGBA")

            # Fit within ICON_PX x ICON_PX while keeping aspect.
            im.thumbnail((ICON_PX, ICON_PX), Image.Resampling.LANCZOS)

            # Center on a square canvas.
            canvas = Image.new("RGBA", (ICON_PX, ICON_PX), (0, 0, 0, 0))
            x = (ICON_PX - im.width) // 2
            y = (ICON_PX - im.height) // 2
            canvas.paste(im, (x, y), im)

            out = BytesIO()
            canvas.save(out, format="PNG", optimize=True)
            data = out.getvalue()

            # Always store icons as PNG for predictable rendering.
            stored_name = unique_store_name(icon_dir, "icon.png")
        except Exception:
            # If we can't parse it as an image, fall back to storing raw bytes.
            stored_name = unique_store_name(icon_dir, file_storage.filename)

        (icon_dir / stored_name).write_bytes(data)
        return f"{ICON_LIBRARY_DIR}/{stored_name}"

    def _save_group_icon_upload(file_storage) -> str:
        if not file_storage or not getattr(file_storage, "filename", ""):
            return ""
        if not _attachment_ext_allowed(file_storage.filename):
            return ""
        icon_dir = current_upload_dir() / ICON_LIBRARY_DIR
        icon_dir.mkdir(parents=True, exist_ok=True)
        max_bytes = int(get_attachment_max_bytes())
        data = file_storage.stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            return ""

        try:
            from io import BytesIO

            from PIL import Image

            ICON_PX = 256
            im = Image.open(BytesIO(data))
            im.load()
            if im.mode not in ("RGBA", "LA"):
                im = im.convert("RGBA")
            im.thumbnail((ICON_PX, ICON_PX), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (ICON_PX, ICON_PX), (0, 0, 0, 0))
            x = (ICON_PX - im.width) // 2
            y = (ICON_PX - im.height) // 2
            canvas.paste(im, (x, y), im)
            out = BytesIO()
            canvas.save(out, format="PNG", optimize=True)
            data = out.getvalue()
            stored_name = unique_store_name(icon_dir, "icon.png")
        except Exception:
            stored_name = unique_store_name(icon_dir, file_storage.filename)

        (icon_dir / stored_name).write_bytes(data)
        return f"{ICON_LIBRARY_DIR}/{stored_name}"

    def _list_icon_library(db=None) -> list[dict]:
        """Return reusable content icons stored under the current DB uploads folder."""
        upload_dir = current_upload_dir()
        rels: set[str] = set()
        for folder in (ICON_LIBRARY_DIR, *LEGACY_ICON_DIRS):
            icon_dir = upload_dir / folder
            if not icon_dir.exists() or not icon_dir.is_dir():
                continue
            for p in icon_dir.iterdir():
                if p.is_file():
                    rels.add(f"{folder}/{p.name}")

        # Include any referenced icons, even if they live in an older folder.
        try:
            use_db = db or get_db()
            for row in use_db.execute(
                "SELECT icon_stored_name FROM links WHERE icon_stored_name IS NOT NULL"
            ).fetchall():
                rel = (row["icon_stored_name"] or "").strip()
                if rel:
                    rels.add(rel)
            for row in use_db.execute(
                "SELECT icon_stored_name FROM link_groups WHERE icon_stored_name IS NOT NULL"
            ).fetchall():
                rel = (row["icon_stored_name"] or "").strip()
                if rel:
                    rels.add(rel)
        except Exception:
            pass

        out = []
        for rel in sorted(rels, key=lambda s: s.lower()):
            p = upload_dir / rel
            if not p.exists() or not p.is_file():
                continue
            out.append({"path": rel, "name": Path(rel).name})
        return out

    def _icon_choice_allowed(icon_choice: str, db=None) -> str:
        icon_choice = (icon_choice or "").strip().lstrip("/\\")
        if not icon_choice or ".." in Path(icon_choice).parts:
            return ""
        allowed = {it["path"] for it in _list_icon_library(db)}
        return icon_choice if icon_choice in allowed else ""

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

        icon_dir = current_upload_dir() / ICON_LIBRARY_DIR
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
                    return f"{ICON_LIBRARY_DIR}/{stored_name}"
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
        # For editor usage we include group context.
        embed_sel = "l.embed" if _links_embed_supported() else "0 AS embed"
        rows = db.execute(
            "SELECT l.id, l.title, l.url, l.target, "
            + embed_sel
            + ", l.item_kind, l.icon_stored_name, l.file_stored_name, l.file_original_name, l.file_mime, l.file_size, l.ha_entity_id, l.ha_entity_type, l.created_at, l.display_order, l.group_id, l.sub_order, "
            "g.name AS group_name, g.display_order AS group_order "
            "FROM links l "
            "LEFT JOIN link_groups g ON g.id = l.group_id "
            "ORDER BY (l.group_id IS NOT NULL) ASC, g.display_order ASC, l.display_order ASC, l.id ASC"
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

    def _reindex_top_item(item_type: str, item_id: int, desired_order: int):
        """Rebuild top-level order for groups + ungrouped links as one list."""
        db = get_db()
        rows = db.execute(
            "SELECT 'group' AS item_type, id, display_order AS ord FROM link_groups "
            "UNION ALL "
            "SELECT 'link' AS item_type, id, display_order AS ord FROM links WHERE group_id IS NULL "
            "ORDER BY ord ASC, item_type ASC, id ASC"
        ).fetchall()
        items = [{"item_type": r["item_type"], "id": int(r["id"])} for r in rows]
        moving = None
        keep = []
        for item in items:
            if item["item_type"] == item_type and item["id"] == int(item_id):
                moving = item
            else:
                keep.append(item)
        if not moving:
            return
        desired = max(0, min(int(desired_order or 0), len(keep)))
        keep.insert(desired, moving)
        for idx, item in enumerate(keep):
            if item["item_type"] == "group":
                db.execute("UPDATE link_groups SET display_order=? WHERE id=?", (idx, item["id"]))
            else:
                db.execute("UPDATE links SET display_order=? WHERE id=? AND group_id IS NULL", (idx, item["id"]))

    def _reindex_group_item(group_id: int, item_id: int, desired_order: int):
        """Rebuild order for links inside one group."""
        db = get_db()
        rows = db.execute(
            "SELECT id FROM links WHERE group_id=? ORDER BY display_order ASC, id ASC",
            (int(group_id),),
        ).fetchall()
        ids = [int(r["id"]) for r in rows]
        try:
            ids.remove(int(item_id))
        except ValueError:
            return
        desired = max(0, min(int(desired_order or 0), len(ids)))
        ids.insert(desired, int(item_id))
        for idx, link_id in enumerate(ids):
            db.execute(
                "UPDATE links SET display_order=? WHERE id=? AND group_id=?",
                (idx, link_id, int(group_id)),
            )

    def _list_top_items(offset: int, limit: int):
        """Top-level icons for /links view: groups + ungrouped links + ungrouped files."""
        db = get_db()
        embed_sel = "COALESCE(embed,0) AS embed" if _links_embed_supported() else "0 AS embed"
        rows = db.execute(
            "SELECT 'group' AS item_type, id, name AS title, NULL AS url, NULL AS target, 0 AS embed, "
            "icon_stored_name, NULL AS item_kind, NULL AS file_mime, NULL AS file_original_name, NULL AS file_size, NULL AS ha_entity_id, NULL AS ha_entity_type, display_order AS ord "
            "FROM link_groups "
            "UNION ALL "
            "SELECT 'link' AS item_type, id, title, url, target, " + embed_sel + ", "
            "icon_stored_name, COALESCE(item_kind,'link') AS item_kind, file_mime, file_original_name, file_size, ha_entity_id, ha_entity_type, display_order AS ord "
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

    @app.route("/content/reorder", methods=["POST"], endpoint="content_reorder")
    def links_reorder():
        gate = _require_write_access(url_for("content"))
        if gate:
            return gate

        try:
            payload = request.get_json(silent=True) or {}
            items = payload.get("items") or []
        except Exception:
            items = []
        if not isinstance(items, list):
            return jsonify({"ok": False, "error": "invalid_payload"}), 400

        db = get_db()
        for idx, item in enumerate(items):
            try:
                item_type = (item.get("item_type") or item.get("type") or "").strip().lower()
                item_id = int(item.get("id"))
                order = int(item.get("order", idx))
            except Exception:
                continue
            if item_type == "group":
                db.execute(
                    "UPDATE link_groups SET display_order=? WHERE id=?",
                    (order, item_id),
                )
            elif item_type == "link":
                group_id = item.get("group_id")
                if group_id is None or str(group_id).strip() == "":
                    db.execute(
                        "UPDATE links SET display_order=? WHERE id=? AND group_id IS NULL",
                        (order, item_id),
                    )
                else:
                    try:
                        group_id_int = int(group_id)
                    except Exception:
                        continue
                    db.execute(
                        "UPDATE links SET display_order=? WHERE id=? AND group_id=?",
                        (order, item_id, group_id_int),
                    )

        db.commit()
        return jsonify({"ok": True})

    @app.route("/content/item/drop-to-group", methods=["POST"], endpoint="content_item_drop_to_group")
    def links_item_drop_to_group():
        gate = _require_write_access(url_for("content"))
        if gate:
            return _json_gate(gate)

        payload = request.get_json(silent=True) or {}
        try:
            item_id = int(payload.get("id"))
            group_id = int(payload.get("group_id"))
        except Exception:
            return jsonify({"ok": False, "error": "invalid_item"}), 400

        db = get_db()
        grow = db.execute("SELECT id FROM link_groups WHERE id=?", (int(group_id),)).fetchone()
        if not grow:
            return jsonify({"ok": False, "error": "group_not_found"}), 404
        row = db.execute("SELECT id FROM links WHERE id=?", (int(item_id),)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "item_not_found"}), 404

        order_row = db.execute(
            "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id=?",
            (int(group_id),),
        ).fetchone()
        order_num = int(order_row["m"] if order_row and order_row["m"] is not None else -1) + 1
        db.execute(
            "UPDATE links SET group_id=?, sub_order=NULL, display_order=? WHERE id=?",
            (int(group_id), int(order_num), int(item_id)),
        )
        db.commit()
        return jsonify({"ok": True, "group_id": int(group_id), "id": int(item_id)})

    @app.route("/content/item/drop-to-root", methods=["POST"], endpoint="content_item_drop_to_root")
    def links_item_drop_to_root():
        gate = _require_write_access(url_for("content"))
        if gate:
            return _json_gate(gate)

        payload = request.get_json(silent=True) or {}
        try:
            item_id = int(payload.get("id"))
        except Exception:
            return jsonify({"ok": False, "error": "invalid_item"}), 400

        db = get_db()
        row = db.execute("SELECT id FROM links WHERE id=?", (int(item_id),)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "item_not_found"}), 404

        order_row = db.execute(
            "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id IS NULL"
        ).fetchone()
        order_num = int(order_row["m"] if order_row and order_row["m"] is not None else -1) + 1
        db.execute(
            "UPDATE links SET group_id=NULL, sub_order=NULL, display_order=? WHERE id=?",
            (int(order_num), int(item_id)),
        )
        db.commit()
        return jsonify({"ok": True, "id": int(item_id)})

    @app.route("/content/item/properties", methods=["POST"], endpoint="content_item_properties")
    def links_item_properties():
        gate = _require_write_access(url_for("content"))
        if gate:
            return gate

        payload = request.get_json(silent=True) or {}
        try:
            item_type = (payload.get("item_type") or payload.get("type") or "").strip().lower()
            item_id = int(payload.get("id"))
        except Exception:
            return jsonify({"ok": False, "error": "invalid_item"}), 400

        title = (payload.get("title") or "").strip()
        page_size = 32
        try:
            page = max(1, int(payload.get("page") or 1))
        except Exception:
            page = 1
        try:
            position = max(0, int(payload.get("position") or 0))
        except Exception:
            position = 0
        position = min(page_size - 1, position)
        desired_order = ((page - 1) * page_size) + position

        db = get_db()
        result_group_id = None
        if item_type == "group":
            row = db.execute("SELECT id FROM link_groups WHERE id=?", (item_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "not_found"}), 404
            if not title:
                return jsonify({"ok": False, "error": "title_required"}), 400
            db.execute(
                "UPDATE link_groups SET name=? WHERE id=?",
                (title, item_id),
            )
            _reindex_top_item("group", item_id, desired_order)
        elif item_type == "link":
            row = db.execute(
                "SELECT id, group_id, item_kind FROM links WHERE id=?",
                (item_id,),
            ).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "not_found"}), 404
            if not title:
                return jsonify({"ok": False, "error": "title_required"}), 400

            group_id = row["group_id"]
            if group_id is None:
                result_group_id = None
            else:
                result_group_id = int(group_id)

            target = (payload.get("target") or "_blank").strip() or "_blank"
            if target not in ("_self", "_blank"):
                target = "_blank"
            kind = row["item_kind"] or "link"
            if kind in ("file", "ha", "camera", "app"):
                db.execute(
                    "UPDATE links SET title=? WHERE id=?",
                    (title, item_id),
                )
            else:
                url = _normalize_url(payload.get("url") or "")
                if not url:
                    return jsonify({"ok": False, "error": "url_required"}), 400
                db.execute(
                    "UPDATE links SET title=?, url=?, target=? WHERE id=?",
                    (title, url, target, item_id),
                )
            if group_id is None:
                _reindex_top_item("link", item_id, desired_order)
            else:
                _reindex_group_item(int(group_id), item_id, desired_order)
        else:
            return jsonify({"ok": False, "error": "invalid_item_type"}), 400

        db.commit()
        if item_type == "group":
            order_row = db.execute(
                "SELECT display_order FROM link_groups WHERE id=?",
                (item_id,),
            ).fetchone()
        else:
            order_row = db.execute(
                "SELECT display_order FROM links WHERE id=?",
                (item_id,),
            ).fetchone()
        final_order = int(order_row["display_order"] if order_row else desired_order)
        new_page = (final_order // page_size) + 1
        return jsonify(
            {
                "ok": True,
                "group_id": result_group_id,
                "page": new_page,
                "position": final_order % page_size,
            }
        )

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

    @app.route("/content", endpoint="content")
    def links():
        gate = _require_read_access(url_for("content"))
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
        items = []
        for row in _list_top_items(offset, page_size):
            item = dict(row)
            item["camera"] = _camera_public_config(row) if (row["item_kind"] or "") == "camera" else {}
            items.append(item)
        return render_template(
            "content.html",
            items=items,
            groups=groups,
            edit_mode=False,
            can_edit=can_edit,
            page=page,
            total_pages=total_pages,
            total_count=total,
            icon_library=_list_icon_library(),
            camera_profiles=_camera_profiles(),
        )

    # Legacy routes (pre-v120): keep old URLs working by redirecting.
    @app.route("/links")
    def links_legacy():
        return redirect(url_for("content"), code=301)

    @app.route("/links/edit", methods=["GET"])
    def links_edit_legacy():
        return redirect(url_for("content_edit"), code=301)

    @app.route("/content/edit", methods=["GET"], endpoint="content_edit")
    def links_edit():
        gate = _require_write_access(url_for("content_edit"))
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
                    "group_id": None,
                    "group_name": "",
                    "order_num": int(g["display_order"] or 0),
                    "sub_order": None,
                    "child_count": len(by_gid.get(int(g["id"]), [])),
                }
            )
            children = sorted(by_gid.get(int(g["id"]), []), key=lambda x: (int(x["display_order"] or 0), int(x["id"])))
            for link in children:
                kind = (link["item_kind"] if ("item_kind" in link.keys()) else "link") or "link"
                is_file = kind == "file"
                is_ha = kind == "ha"
                is_camera = kind == "camera"
                file_size = link["file_size"] if ("file_size" in link.keys()) else None
                size_mb = round((float(file_size) / (1024.0 * 1024.0)), 2) if (file_size is not None) else None
                out.append(
                    {
                        "row_type": "file" if is_file else ("ha" if is_ha else ("camera" if is_camera else "link")),
                        "id": int(link["id"]),
                        "title": link["title"],
                        "url": link["url"],
                        "target": link["target"],
                        "embed": int(link["embed"] or 0),
                        "icon": link["icon_stored_name"],
                        "group_id": int(link["group_id"]),
                        "group_name": link["group_name"],
                        "order_num": int(link["display_order"] or 0),
                        "sub_order": None,
                        "item_kind": kind,
                        "file_mime": (link["file_mime"] if ("file_mime" in link.keys()) else None),
                        "file_original_name": (
                            link["file_original_name"] if ("file_original_name" in link.keys()) else None
                        ),
                        "file_size": int(file_size) if file_size is not None else None,
                        "file_size_mb": size_mb,
                        "ha_entity_id": (link["ha_entity_id"] if ("ha_entity_id" in link.keys()) else None),
                        "ha_entity_type": (link["ha_entity_type"] if ("ha_entity_type" in link.keys()) else None),
                        "camera": _camera_editor_config(link) if is_camera else {},
                    }
                )

        ungrouped = [r for r in rows if r["group_id"] is None]
        ungrouped.sort(key=lambda x: (int(x["display_order"] or 0), int(x["id"])))
        for link in ungrouped:
            kind = (link["item_kind"] if ("item_kind" in link.keys()) else "link") or "link"
            is_file = kind == "file"
            is_ha = kind == "ha"
            is_camera = kind == "camera"
            file_size = link["file_size"] if ("file_size" in link.keys()) else None
            size_mb = round((float(file_size) / (1024.0 * 1024.0)), 2) if (file_size is not None) else None
            out.append(
                {
                    "row_type": "file" if is_file else ("ha" if is_ha else ("camera" if is_camera else "link")),
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
                    "ha_entity_id": (link["ha_entity_id"] if ("ha_entity_id" in link.keys()) else None),
                    "ha_entity_type": (link["ha_entity_type"] if ("ha_entity_type" in link.keys()) else None),
                    "camera": _camera_editor_config(link) if is_camera else {},
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
        # Paginate editor table by top-level rows only. Group children are rendered
        # with their parent so collapsed children don't spill onto later pages.
        try:
            page = int(request.args.get("page", "1") or "1")
        except Exception:
            page = 1
        page = max(1, page)
        page_size = 32
        top_level_rows = [r for r in out if r.get("group_id") is None]
        children_by_gid = {}
        for r in out:
            gid = r.get("group_id")
            if gid is not None:
                children_by_gid.setdefault(int(gid), []).append(r)

        total = len(top_level_rows)
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        start = (page - 1) * page_size
        end = start + page_size
        page_rows = []
        for r in top_level_rows[start:end]:
            page_rows.append(r)
            if r.get("row_type") == "group":
                page_rows.extend(children_by_gid.get(int(r["id"]), []))

        return render_template(
            "content.html",
            groups=groups,
            editor_rows=page_rows,
            edit_mode=True,
            can_edit=True,
            page=page,
            total_pages=total_pages,
            filters=filters,
            filtered_selections=[f"{r.get('row_type')}:{r.get('id')}" for r in out],
            icon_library=_list_icon_library(),
            camera_profiles=_camera_profiles(),
        )

    @app.route("/content/group/<int:group_id>/items", endpoint="content_group_items")
    def links_group_items(group_id: int):
        gate = _require_read_access(url_for("content"))
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
            "SELECT id, title, url, target, " + embed_sel + ", icon_stored_name, display_order, "
            "item_kind, file_mime, file_original_name, file_size, ha_entity_id, ha_entity_type "
            "FROM links WHERE group_id=? "
            "ORDER BY display_order ASC, id ASC "
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
                "order_num": int(r["display_order"] or 0),
                "item_kind": (r["item_kind"] or "link"),
                "file_mime": r["file_mime"],
                "file_original_name": r["file_original_name"],
                "file_size": int(r["file_size"] or 0) if (r["file_size"] is not None) else None,
                "ha_entity_id": r["ha_entity_id"],
                "ha_entity_type": r["ha_entity_type"],
                "camera": _camera_public_config(r) if (r["item_kind"] or "") == "camera" else {},
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
                "total_count": total,
            }
        )

    @app.route("/content/ha/entities", endpoint="content_ha_entities")
    def content_ha_entities():
        gate = _require_write_access(url_for("content_edit"))
        if gate:
            return _json_gate(gate)

        entity_type = (request.args.get("type") or "scene").strip().lower()
        if entity_type not in {"scene", "script"}:
            return jsonify({"ok": False, "error": "invalid_type"}), 400

        try:
            states = call_home_assistant(load_config(), "/api/states", method="GET", timeout=6) or []
        except HomeAssistantError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 502
        if not isinstance(states, list):
            return jsonify({"ok": False, "error": "unexpected_response"}), 502

        prefix = entity_type + "."
        entities = []
        for row in states:
            if not isinstance(row, dict):
                continue
            entity_id = str(row.get("entity_id") or "")
            if not entity_id.startswith(prefix):
                continue
            attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
            friendly = str(attrs.get("friendly_name") or entity_id)
            entities.append(
                {
                    "entity_id": entity_id,
                    "name": friendly,
                    "icon": str(attrs.get("icon") or ""),
                    "type": entity_type,
                }
            )
        entities.sort(key=lambda item: (item["name"].lower(), item["entity_id"]))
        return jsonify({"ok": True, "entities": entities})

    @app.route("/content/ha/activate", methods=["POST"], endpoint="content_ha_activate")
    def content_ha_activate():
        # Activating an HA scene/script does not modify the selected DB, so
        # users with explicit read-without-password permission may use tiles.
        gate = _require_read_access(url_for("content"))
        if gate:
            return _json_gate(gate)

        payload = request.get_json(silent=True) or {}
        try:
            item_id = int(payload.get("id") or 0)
        except Exception:
            item_id = 0
        if item_id <= 0:
            return jsonify({"ok": False, "error": "missing_item"}), 400

        row = (
            get_db()
            .execute(
                "SELECT id, title, item_kind, ha_entity_id, ha_entity_type FROM links WHERE id=?",
                (int(item_id),),
            )
            .fetchone()
        )
        if not row or (row["item_kind"] or "") != "ha":
            return jsonify({"ok": False, "error": "not_found"}), 404

        entity_id = (row["ha_entity_id"] or "").strip()
        entity_type = (row["ha_entity_type"] or "").strip().lower()
        if entity_type not in {"scene", "script"} or not entity_id.startswith(entity_type + "."):
            return jsonify({"ok": False, "error": "invalid_entity"}), 400

        try:
            call_home_assistant(
                load_config(),
                f"/api/services/{entity_type}/turn_on",
                method="POST",
                payload={"entity_id": entity_id},
                timeout=6,
            )
        except HomeAssistantError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 502
        return jsonify({"ok": True, "title": row["title"], "entity_id": entity_id})

    @app.route("/content/camera/<int:item_id>/snapshot", endpoint="content_camera_snapshot")
    def content_camera_snapshot(item_id: int):
        gate = _require_read_access(url_for("content"))
        if gate:
            return gate

        row = get_db().execute("SELECT id, title, url, item_kind FROM links WHERE id=?", (int(item_id),)).fetchone()
        cfg = _camera_config_from_row(row)
        if (cfg.get("vendor") or cfg.get("kind") or "generic") != "reolink":
            return ("Snapshots are only configured for Reolink cameras.", 400)
        if not cfg.get("base_url"):
            return ("Camera URL is missing or not local.", 400)
        snap_url = _camera_reolink_url(cfg, "Snap")
        if not snap_url:
            return ("Camera snapshot is not configured.", 400)
        try:
            req = Request(snap_url, headers={"User-Agent": "Vortnotes/Camera"})
            with urlopen(req, timeout=8) as resp:
                data = _read_camera_snapshot(resp)
                ctype = (resp.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0].strip()
        except Exception as exc:
            return (f"Camera snapshot failed: {exc}", 502)
        if (not ctype.startswith("image/") or not _looks_like_image(data)) and _camera_response_is_login_failed(data):
            token = ""
            try:
                token = _camera_reolink_login_token(cfg)
                if token:
                    token_url = _camera_reolink_url(cfg, "Snap", token=token, include_credentials=False)
                    req = Request(token_url, headers={"User-Agent": "Vortnotes/Camera"})
                    with urlopen(req, timeout=8) as resp:
                        data = _read_camera_snapshot(resp)
                        ctype = (resp.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0].strip()
            except Exception:
                pass
            finally:
                _camera_reolink_logout_token(cfg, token)
        if not ctype.startswith("image/") or not _looks_like_image(data):
            detail = _short_camera_body(data)
            if not detail:
                detail = f"Camera returned {ctype or 'an empty response'} instead of an image."
            return (detail, 502, {"Content-Type": "text/plain; charset=utf-8"})
        return Response(data, mimetype=ctype, headers={"Cache-Control": "no-store"})

    @app.route("/content/camera/<int:item_id>/stream/start", methods=["POST"], endpoint="content_camera_stream_start")
    def content_camera_stream_start(item_id: int):
        gate = _require_read_access(url_for("content"))
        if gate:
            return _json_gate(gate)
        row = get_db().execute("SELECT id, title, url, item_kind FROM links WHERE id=?", (int(item_id),)).fetchone()
        cfg = _camera_config_from_row(row)
        if (cfg.get("playback_mode") or "snapshot") != "rtsp":
            return jsonify({"ok": False, "error": "rtsp_playback_not_configured"}), 400
        ok, error = _camera_start_hls_stream(int(item_id), cfg)
        if not ok:
            return jsonify({"ok": False, "error": error}), 502
        playlist = url_for("content_camera_hls_file", item_id=int(item_id), filename="stream.m3u8")
        return jsonify({"ok": True, "playlist": playlist})

    @app.route("/content/camera/<int:item_id>/stream/stop", methods=["POST"], endpoint="content_camera_stream_stop")
    def content_camera_stream_stop(item_id: int):
        gate = _require_read_access(url_for("content"))
        if gate:
            return _json_gate(gate)
        with _camera_streams_lock:
            _camera_stop_stream_locked(int(item_id))
        return jsonify({"ok": True})

    @app.route("/content/camera/<int:item_id>/hls/<path:filename>", endpoint="content_camera_hls_file")
    def content_camera_hls_file(item_id: int, filename: str):
        gate = _require_read_access(url_for("content"))
        if gate:
            return _plain_auth_gate(gate)
        filename = (filename or "").replace("\\", "/")
        if filename.startswith("/") or ".." in filename.split("/"):
            abort(404)
        with _camera_streams_lock:
            entry = _camera_streams.get(int(item_id))
            if entry:
                entry["last_seen"] = time.time()
        directory = _camera_hls_dir(int(item_id))
        if not (directory / filename).is_file():
            return ("Stream is starting.", 503, {"Cache-Control": "no-store"})
        mimetype = "application/vnd.apple.mpegurl" if filename.endswith(".m3u8") else "video/mp2t"
        return send_from_directory(directory, filename, mimetype=mimetype, max_age=0)

    @app.route("/content/camera/<int:item_id>/ptz", methods=["POST"], endpoint="content_camera_ptz")
    def content_camera_ptz(item_id: int):
        gate = _require_read_access(url_for("content"))
        if gate:
            return _json_gate(gate)

        payload = request.get_json(silent=True) or {}
        action = (payload.get("action") or "").strip().lower()
        op_map = {
            "up": "Up",
            "down": "Down",
            "left": "Left",
            "right": "Right",
            "stop": "Stop",
            "zoom_in": "ZoomInc",
            "zoom_out": "ZoomDec",
        }
        op = op_map.get(action)
        if not op:
            return jsonify({"ok": False, "error": "invalid_action"}), 400
        try:
            speed = max(1, min(64, int(payload.get("speed") or 32)))
        except Exception:
            speed = 32

        row = get_db().execute("SELECT id, title, url, item_kind FROM links WHERE id=?", (int(item_id),)).fetchone()
        cfg = _camera_config_from_row(row)
        if not cfg.get("base_url") or (cfg.get("ptz_mode") or "none") != "reolink":
            return jsonify({"ok": False, "error": "ptz_not_configured"}), 400

        ptz_param = {"channel": int(cfg.get("channel") or 0), "op": op, "speed": speed}
        data = b""
        try:
            token = ""
            if cfg.get("username") and cfg.get("password"):
                token = _camera_reolink_login_token(cfg)
                if token:
                    try:
                        data = _camera_reolink_json_command(cfg, "PtzCtrl", ptz_param, token)
                    finally:
                        _camera_reolink_logout_token(cfg, token)
            error = _camera_reolink_error(data)
            if not data or error:
                ptz_url = _camera_reolink_url(cfg, "PtzCtrl", {"op": op, "speed": speed})
                req = Request(ptz_url, headers={"User-Agent": "Vortnotes/Camera"})
                with urlopen(req, timeout=5) as resp:
                    data = resp.read(512 * 1024)
            error = _camera_reolink_error(data)
            if _camera_response_is_login_failed(data):
                return jsonify({"ok": False, "error": _short_camera_body(data) or "Camera PTZ login failed."}), 502
            if error:
                return jsonify({"ok": False, "error": error}), 502
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Camera PTZ failed: {exc}"}), 502
        return jsonify({"ok": True, "action": action})

    @app.route("/content/item/save", methods=["POST"], endpoint="content_item_save")
    def links_item_save():
        gate = _require_write_access(url_for("content_edit"))
        if gate:
            return gate

        db = get_db()

        mode = (request.form.get("mode") or "add").strip().lower()
        row_type = (request.form.get("row_type") or "link").strip().lower()
        rid_raw = (request.form.get("id") or "").strip()
        rid = int(rid_raw) if (rid_raw.isdigit()) else None
        return_to = (request.form.get("return_to") or "").strip()
        if not return_to.startswith("/") or return_to.startswith("//"):
            return_to = request.referrer or url_for("content")
            try:
                parsed_return = urlparse(return_to)
                if parsed_return.scheme or parsed_return.netloc:
                    return_to = parsed_return.path or url_for("content")
                    if parsed_return.query:
                        return_to += "?" + parsed_return.query
            except Exception:
                return_to = url_for("content")
            if return_to == request.path or return_to.startswith("//") or not return_to.startswith("/"):
                return_to = url_for("content")

        def _save_redirect():
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"ok": True, "redirect": return_to})
            return redirect(return_to)

        def _int_or_none(v: str):
            v = (v or "").strip()
            if not v:
                return None
            try:
                return int(v)
            except Exception:
                return None

        order_num = _int_or_none(request.form.get("order_num"))
        embed = 1 if (request.form.get("embed") or "").strip() in ("1", "true", "on", "yes") else 0

        icon_action = (request.form.get("icon_action") or "keep").strip().lower()
        clear_icon = icon_action == "clear"

        if row_type == "group":
            name = (request.form.get("title") or "").strip()
            if not name:
                return _save_redirect()

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

            if clear_icon:
                icon_stored = ""
            elif icon_action == "library":
                icon_stored = _icon_choice_allowed(request.form.get("icon_choice") or "", db)
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

            db.commit()
            return _save_redirect()

        if row_type == "file":
            # File item (stored in uploads; rendered in Content grid)
            title = (request.form.get("title") or "").strip()
            group_id = _int_or_none(request.form.get("group_id"))
            target = "_blank"

            # Existing file info (edit mode)
            old_file_rel = ""
            if mode == "edit" and rid is not None:
                ex = db.execute(
                    "SELECT icon_stored_name, file_stored_name FROM links WHERE id=?",
                    (int(rid),),
                ).fetchone()
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
                if order_num is None:
                    row = db.execute(
                        "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id=?", (int(group_id),)
                    ).fetchone()
                    order_num = int(row["m"] if row and row["m"] is not None else -1) + 1
                sub_order_val = None
                _shift_orders(
                    table="links",
                    col="display_order",
                    desired=int(order_num),
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
            elif icon_action == "library":
                icon_stored = _icon_choice_allowed(request.form.get("icon_choice") or "", db)
            elif icon_action == "upload" and icon_file and getattr(icon_file, "filename", ""):
                icon_stored = _save_icon_upload(icon_file)

            def _store_content_file(file_storage):
                if not file_storage or not getattr(file_storage, "filename", ""):
                    return None
                if not _attachment_ext_allowed(file_storage.filename):
                    return None
                files_dir = current_upload_dir() / "content_files"
                files_dir.mkdir(parents=True, exist_ok=True)
                stored_name = unique_store_name(files_dir, file_storage.filename)
                max_bytes = int(get_attachment_max_bytes())
                data = file_storage.stream.read(max_bytes + 1)
                if len(data) > max_bytes:
                    return None
                (files_dir / stored_name).write_bytes(data)
                return {
                    "rel": f"content_files/{stored_name}",
                    "orig": file_storage.filename,
                    "mime": (getattr(file_storage, "mimetype", "") or "").strip() or "application/octet-stream",
                    "size": int(len(data)),
                }

            # File upload (required for add; optional for edit)
            file_uploads = [f for f in request.files.getlist("file_file") if getattr(f, "filename", "")]
            file_rel = old_file_rel
            file_orig = None
            file_mime = None
            file_size = None
            stored_files = []
            if mode == "add":
                for f in file_uploads:
                    stored = _store_content_file(f)
                    if stored is None:
                        return _save_redirect()
                    stored_files.append(stored)
            elif file_uploads:
                stored = _store_content_file(file_uploads[0])
                if stored is None:
                    return _save_redirect()
                file_rel = stored["rel"]
                file_orig = stored["orig"]
                file_mime = stored["mime"]
                file_size = stored["size"]
                if not title:
                    title = stored["orig"]

            if mode == "add":
                if not stored_files:
                    return _save_redirect()
                for idx, stored in enumerate(stored_files):
                    item_title = title if (title and len(stored_files) == 1) else stored["orig"]
                    item_order = int(order_num) + idx
                    if idx > 0:
                        _shift_orders(
                            table="links",
                            col="display_order",
                            desired=item_order,
                            where_sql="group_id IS NULL" if group_id is None else "group_id = ?",
                            where_params=() if group_id is None else (int(group_id),),
                        )
                    db.execute(
                        "INSERT INTO links (title, url, group_id, sub_order, target, icon_stored_name, created_at, display_order, item_kind, file_stored_name, file_original_name, file_mime, file_size) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            item_title,
                            stored["rel"],
                            group_id,
                            sub_order_val,
                            target,
                            icon_stored or None,
                            iso_now(),
                            item_order,
                            "file",
                            stored["rel"],
                            stored["orig"],
                            stored["mime"],
                            stored["size"],
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
                        "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, icon_stored_name=NULL, display_order=?, item_kind='file', file_stored_name=?, file_original_name=?, file_mime=?, file_size=?, ha_entity_id=NULL, ha_entity_type=NULL WHERE id=?",
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
                        "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, icon_stored_name=?, display_order=?, item_kind='file', file_stored_name=?, file_original_name=?, file_mime=?, file_size=?, ha_entity_id=NULL, ha_entity_type=NULL WHERE id=?",
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
                        "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, display_order=?, item_kind='file', file_stored_name=?, file_original_name=?, file_mime=?, file_size=?, ha_entity_id=NULL, ha_entity_type=NULL WHERE id=?",
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

                # Remove old file if replaced
                try:
                    if file_rel and old_file_rel and file_rel != old_file_rel:
                        p = current_upload_dir() / old_file_rel
                        if p.exists() and p.is_file():
                            p.unlink()
                except Exception:
                    pass

            db.commit()
            return _save_redirect()

        if row_type == "camera":
            title = (request.form.get("title") or "").strip() or "Camera"
            cfg = _camera_config_from_form(request.form)
            if not cfg:
                flash("Select a saved camera profile. Camera settings are managed in Settings.", "error")
                return _save_redirect()
            group_id = _int_or_none(request.form.get("group_id"))
            target = "_self"
            config_json = json.dumps(cfg, separators=(",", ":"))

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
                if order_num is None:
                    row = db.execute(
                        "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id=?", (int(group_id),)
                    ).fetchone()
                    order_num = int(row["m"] if row and row["m"] is not None else -1) + 1
                sub_order_val = None
                _shift_orders(
                    table="links",
                    col="display_order",
                    desired=int(order_num),
                    where_sql="group_id = ?",
                    where_params=(int(group_id),),
                    exclude_id=rid if (mode == "edit" and rid is not None) else None,
                )

            icon_file = request.files.get("icon_file")
            icon_stored = ""
            if clear_icon:
                icon_stored = ""
            elif icon_action == "library":
                icon_stored = _icon_choice_allowed(request.form.get("icon_choice") or "", db)
            elif icon_action == "upload" and icon_file and getattr(icon_file, "filename", ""):
                icon_stored = _save_icon_upload(icon_file)

            old_file_rel = ""
            if mode == "edit" and rid is not None:
                ex = db.execute("SELECT file_stored_name FROM links WHERE id=?", (int(rid),)).fetchone()
                old_file_rel = (ex["file_stored_name"] if ex else "") or ""

            if mode == "add":
                db.execute(
                    "INSERT INTO links (title, url, group_id, sub_order, target, icon_stored_name, created_at, display_order, item_kind, embed) "
                    "VALUES (?,?,?,?,?,?,?,?,?,0)",
                    (
                        title,
                        config_json,
                        group_id,
                        sub_order_val,
                        target,
                        icon_stored or None,
                        iso_now(),
                        int(order_num),
                        "camera",
                    ),
                )
            elif mode == "edit" and rid is not None:
                if clear_icon:
                    db.execute(
                        "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, icon_stored_name=NULL, display_order=?, embed=0, item_kind='camera', file_stored_name=NULL, file_original_name=NULL, file_mime=NULL, file_size=NULL, ha_entity_id=NULL, ha_entity_type=NULL WHERE id=?",
                        (title, config_json, group_id, sub_order_val, target, int(order_num), int(rid)),
                    )
                elif icon_stored:
                    db.execute(
                        "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, icon_stored_name=?, display_order=?, embed=0, item_kind='camera', file_stored_name=NULL, file_original_name=NULL, file_mime=NULL, file_size=NULL, ha_entity_id=NULL, ha_entity_type=NULL WHERE id=?",
                        (title, config_json, group_id, sub_order_val, target, icon_stored, int(order_num), int(rid)),
                    )
                else:
                    db.execute(
                        "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, display_order=?, embed=0, item_kind='camera', file_stored_name=NULL, file_original_name=NULL, file_mime=NULL, file_size=NULL, ha_entity_id=NULL, ha_entity_type=NULL WHERE id=?",
                        (title, config_json, group_id, sub_order_val, target, int(order_num), int(rid)),
                    )
                _unlink_upload(old_file_rel)

            db.commit()
            return _save_redirect()

        if row_type == "app":
            app_id = (request.form.get("app_id") or request.form.get("url") or "").strip().lower()
            app_info = CONTENT_APPS.get(app_id)
            if not app_info:
                return _save_redirect()
            title = (request.form.get("title") or "").strip() or app_info["title"]
            group_id = _int_or_none(request.form.get("group_id"))

            if group_id is None:
                if order_num is None:
                    row = db.execute(
                        "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id IS NULL"
                    ).fetchone()
                    order_num = int(row["m"] if row and row["m"] is not None else -1) + 1
                where_sql, where_params = "group_id IS NULL", ()
            else:
                if order_num is None:
                    row = db.execute(
                        "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id=?",
                        (int(group_id),),
                    ).fetchone()
                    order_num = int(row["m"] if row and row["m"] is not None else -1) + 1
                where_sql, where_params = "group_id = ?", (int(group_id),)

            _shift_orders(
                table="links",
                col="display_order",
                desired=int(order_num),
                where_sql=where_sql,
                where_params=where_params,
                exclude_id=rid if mode == "edit" and rid is not None else None,
            )

            if mode == "add":
                db.execute(
                    "INSERT INTO links (title, url, group_id, sub_order, target, icon_stored_name, created_at, display_order, item_kind, embed) "
                    "VALUES (?,?,?,?,?,?,?,?,?,0)",
                    (title, app_id, group_id, None, "_self", None, iso_now(), int(order_num), "app"),
                )
            elif mode == "edit" and rid is not None:
                db.execute(
                    "UPDATE links SET title=?, url=?, group_id=?, sub_order=NULL, target='_self', display_order=?, "
                    "item_kind='app', embed=0, file_stored_name=NULL, file_original_name=NULL, file_mime=NULL, "
                    "file_size=NULL, ha_entity_id=NULL, ha_entity_type=NULL WHERE id=?",
                    (title, app_id, group_id, int(order_num), int(rid)),
                )
            db.commit()
            return _save_redirect()

        if row_type == "ha":
            title = (request.form.get("title") or "").strip()
            entity_type = (request.form.get("ha_entity_type") or "scene").strip().lower()
            entity_id = (request.form.get("ha_entity_id") or "").strip()
            if entity_type not in {"scene", "script"}:
                return _save_redirect()
            if not entity_id.startswith(entity_type + "."):
                return _save_redirect()
            if not title:
                title = entity_id
            group_id = _int_or_none(request.form.get("group_id"))
            url = f"ha://{entity_type}/{entity_id}"
            target = "_self"

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
                if order_num is None:
                    row = db.execute(
                        "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id=?", (int(group_id),)
                    ).fetchone()
                    order_num = int(row["m"] if row and row["m"] is not None else -1) + 1
                sub_order_val = None
                _shift_orders(
                    table="links",
                    col="display_order",
                    desired=int(order_num),
                    where_sql="group_id = ?",
                    where_params=(int(group_id),),
                    exclude_id=rid if (mode == "edit" and rid is not None) else None,
                )

            icon_file = request.files.get("icon_file")
            icon_stored = ""
            if clear_icon:
                icon_stored = ""
            elif icon_action == "library":
                icon_stored = _icon_choice_allowed(request.form.get("icon_choice") or "", db)
            elif icon_action == "upload" and icon_file and getattr(icon_file, "filename", ""):
                icon_stored = _save_icon_upload(icon_file)

            old_file_rel = ""
            if mode == "edit" and rid is not None:
                ex = db.execute("SELECT file_stored_name FROM links WHERE id=?", (int(rid),)).fetchone()
                old_file_rel = (ex["file_stored_name"] if ex else "") or ""

            if mode == "add":
                db.execute(
                    "INSERT INTO links (title, url, group_id, sub_order, target, icon_stored_name, created_at, display_order, item_kind, ha_entity_id, ha_entity_type) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        title,
                        url,
                        group_id,
                        sub_order_val,
                        target,
                        icon_stored or None,
                        iso_now(),
                        int(order_num),
                        "ha",
                        entity_id,
                        entity_type,
                    ),
                )
            elif mode == "edit" and rid is not None:
                if clear_icon:
                    db.execute(
                        "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, icon_stored_name=NULL, display_order=?, embed=0, item_kind='ha', file_stored_name=NULL, file_original_name=NULL, file_mime=NULL, file_size=NULL, ha_entity_id=?, ha_entity_type=? WHERE id=?",
                        (title, url, group_id, sub_order_val, target, int(order_num), entity_id, entity_type, int(rid)),
                    )
                elif icon_stored:
                    db.execute(
                        "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, icon_stored_name=?, display_order=?, embed=0, item_kind='ha', file_stored_name=NULL, file_original_name=NULL, file_mime=NULL, file_size=NULL, ha_entity_id=?, ha_entity_type=? WHERE id=?",
                        (
                            title,
                            url,
                            group_id,
                            sub_order_val,
                            target,
                            icon_stored,
                            int(order_num),
                            entity_id,
                            entity_type,
                            int(rid),
                        ),
                    )
                else:
                    db.execute(
                        "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, display_order=?, embed=0, item_kind='ha', file_stored_name=NULL, file_original_name=NULL, file_mime=NULL, file_size=NULL, ha_entity_id=?, ha_entity_type=? WHERE id=?",
                        (title, url, group_id, sub_order_val, target, int(order_num), entity_id, entity_type, int(rid)),
                    )
                _unlink_upload(old_file_rel)

            db.commit()
            return _save_redirect()

        # link
        url = _normalize_url(request.form.get("url") or "")
        title = (request.form.get("title") or "").strip()
        if not url:
            return _save_redirect()
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
            if order_num is None:
                row = db.execute(
                    "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id=?", (int(group_id),)
                ).fetchone()
                order_num = int(row["m"] if row and row["m"] is not None else -1) + 1
            sub_order_val = None

            # If requested order conflicts within the same group, shift others down.
            _shift_orders(
                table="links",
                col="display_order",
                desired=int(order_num),
                where_sql="group_id = ?",
                where_params=(int(group_id),),
                exclude_id=rid if (mode == "edit" and rid is not None) else None,
            )

        icon_file = request.files.get("icon_file")
        old_file_rel = ""
        if mode == "edit" and rid is not None:
            ex = db.execute("SELECT icon_stored_name, file_stored_name FROM links WHERE id=?", (int(rid),)).fetchone()
            old_file_rel = (ex["file_stored_name"] if ex else "") or ""

        icon_stored = ""
        if clear_icon:
            icon_stored = ""
        elif icon_action == "library":
            icon_stored = _icon_choice_allowed(request.form.get("icon_choice") or "", db)
        elif icon_action == "upload" and icon_file and getattr(icon_file, "filename", ""):
            icon_stored = _save_icon_upload(icon_file)
        elif icon_action == "grab":
            icon_stored = _try_fetch_favicon(url)

        if mode == "add":
            db.execute(
                "INSERT INTO links (title, url, group_id, sub_order, target, icon_stored_name, created_at, display_order, item_kind, ha_entity_id, ha_entity_type) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    title,
                    url,
                    group_id,
                    sub_order_val,
                    target,
                    icon_stored or None,
                    iso_now(),
                    int(order_num),
                    "link",
                    None,
                    None,
                ),
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
                    "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, embed=?, icon_stored_name=NULL, display_order=?, item_kind='link', file_stored_name=NULL, file_original_name=NULL, file_mime=NULL, file_size=NULL, ha_entity_id=NULL, ha_entity_type=NULL WHERE id=?",
                    (title, url, group_id, sub_order_val, target, int(embed), int(order_num), int(rid)),
                )
            elif icon_stored:
                db.execute(
                    "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, embed=?, icon_stored_name=?, display_order=?, item_kind='link', file_stored_name=NULL, file_original_name=NULL, file_mime=NULL, file_size=NULL, ha_entity_id=NULL, ha_entity_type=NULL WHERE id=?",
                    (title, url, group_id, sub_order_val, target, int(embed), icon_stored, int(order_num), int(rid)),
                )
            else:
                db.execute(
                    "UPDATE links SET title=?, url=?, group_id=?, sub_order=?, target=?, embed=?, display_order=?, item_kind='link', file_stored_name=NULL, file_original_name=NULL, file_mime=NULL, file_size=NULL, ha_entity_id=NULL, ha_entity_type=NULL WHERE id=?",
                    (title, url, group_id, sub_order_val, target, int(embed), int(order_num), int(rid)),
                )
            _unlink_upload(old_file_rel)
        db.commit()
        return _save_redirect()

    def _parse_selected_items() -> list[tuple[str, int]]:
        raw_values = []
        raw_values.extend(request.form.getlist("selected_items"))
        selected = (request.form.get("selected") or "").strip()
        if selected:
            raw_values.extend([part.strip() for part in selected.split(",") if part.strip()])

        parsed = []
        seen = set()
        for raw in raw_values:
            if not raw or ":" not in raw:
                continue
            row_type, rid_raw = raw.split(":", 1)
            row_type = row_type.strip().lower()
            try:
                rid = int(rid_raw)
            except Exception:
                continue
            if row_type not in {"group", "link", "file", "ha", "app", "camera"}:
                continue
            key = (row_type, rid)
            if key in seen:
                continue
            seen.add(key)
            parsed.append(key)
        return parsed

    def _unlink_upload(rel: str | None) -> None:
        if not rel:
            return
        try:
            p = current_upload_dir() / rel
            if p.exists() and p.is_file():
                p.unlink()
        except Exception:
            pass

    def _delete_link_record(db, link_id: int) -> None:
        row = db.execute(
            "SELECT icon_stored_name, file_stored_name FROM links WHERE id=?",
            (int(link_id),),
        ).fetchone()
        if not row:
            return
        f_rel = (row["file_stored_name"] if row else "") or ""
        db.execute("DELETE FROM links WHERE id=?", (int(link_id),))
        _unlink_upload(f_rel)

    def _delete_group_record(db, group_id: int, delete_contents: bool) -> None:
        row = db.execute("SELECT icon_stored_name FROM link_groups WHERE id=?", (int(group_id),)).fetchone()
        if not row:
            return
        if delete_contents:
            child_rows = db.execute(
                "SELECT id FROM links WHERE group_id=?",
                (int(group_id),),
            ).fetchall()
            for child in child_rows:
                _delete_link_record(db, int(child["id"]))
        else:
            db.execute("UPDATE links SET group_id=NULL, sub_order=NULL WHERE group_id=?", (int(group_id),))
        db.execute("DELETE FROM link_groups WHERE id=?", (int(group_id),))

    @app.route("/content/item/delete", methods=["POST"], endpoint="content_item_delete")
    def links_item_delete():
        gate = _require_write_access(url_for("content_edit"))
        if gate:
            return gate

        return_to = (request.form.get("return_to") or "").strip()
        if not return_to.startswith("/") or return_to.startswith("//"):
            return_to = url_for("content_edit")

        items = _parse_selected_items()
        if not items:
            return redirect(return_to)

        db = get_db()
        delete_group_contents = (request.form.get("delete_group_contents") or "").strip() == "1"
        for row_type, rid in items:
            if row_type == "group":
                _delete_group_record(db, rid, delete_group_contents)
        else:
            _delete_link_record(db, rid)
        db.commit()
        return redirect(return_to)

    @app.route("/content/item/move", methods=["POST"], endpoint="content_items_move")
    def links_items_move():
        gate = _require_write_access(url_for("content_edit"))
        if gate:
            return gate

        items = [(row_type, rid) for row_type, rid in _parse_selected_items() if row_type != "group"]
        if not items:
            return redirect(url_for("content_edit"))

        group_raw = (request.form.get("group_id") or "").strip()
        group_id = None
        if group_raw:
            try:
                group_id = int(group_raw)
            except Exception:
                group_id = None

        db = get_db()
        if group_id is not None:
            grow = db.execute("SELECT id FROM link_groups WHERE id=?", (int(group_id),)).fetchone()
            if not grow:
                group_id = None

        if group_id is None:
            row = db.execute(
                "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id IS NULL"
            ).fetchone()
        else:
            row = db.execute(
                "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id=?", (int(group_id),)
            ).fetchone()
        order_num = int(row["m"] if row and row["m"] is not None else -1) + 1

        for _, rid in items:
            db.execute(
                "UPDATE links SET group_id=?, sub_order=NULL, display_order=? WHERE id=?",
                (group_id, order_num, int(rid)),
            )
            order_num += 1
        db.commit()
        return redirect(url_for("content_edit"))

    @app.route("/content/item/bulk-edit", methods=["POST"], endpoint="content_items_bulk_edit")
    def links_items_bulk_edit():
        gate = _require_write_access(url_for("content_edit"))
        if gate:
            return gate

        items = _parse_selected_items()
        if not items:
            return redirect(url_for("content_edit"))

        apply_group = (request.form.get("apply_group") or "").strip() == "1"
        group_raw = (request.form.get("group_id") or "").strip()
        group_id = None
        if group_raw:
            try:
                group_id = int(group_raw)
            except Exception:
                group_id = None

        icon_action = (request.form.get("icon_action") or "keep").strip().lower()
        icon_file = request.files.get("icon_file")
        icon_stored = ""
        if icon_action == "library":
            icon_stored = _icon_choice_allowed(request.form.get("icon_choice") or "", db=None)
            if not icon_stored:
                return redirect(url_for("content_edit"))
        elif icon_action == "upload":
            if not icon_file or not getattr(icon_file, "filename", ""):
                return redirect(url_for("content_edit"))
            icon_stored = _save_icon_upload(icon_file)
            if not icon_stored:
                return redirect(url_for("content_edit"))

        db = get_db()
        if group_id is not None:
            grow = db.execute("SELECT id FROM link_groups WHERE id=?", (int(group_id),)).fetchone()
            if not grow:
                group_id = None

        next_order = None
        if apply_group:
            if group_id is None:
                row = db.execute(
                    "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id IS NULL"
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT COALESCE(MAX(display_order), -1) AS m FROM links WHERE group_id=?", (int(group_id),)
                ).fetchone()
            next_order = int(row["m"] if row and row["m"] is not None else -1) + 1

        for row_type, rid in items:
            if row_type == "group":
                if icon_action in {"clear", "upload"}:
                    if icon_action == "clear":
                        db.execute("UPDATE link_groups SET icon_stored_name=NULL WHERE id=?", (int(rid),))
                    else:
                        db.execute("UPDATE link_groups SET icon_stored_name=? WHERE id=?", (icon_stored, int(rid)))
                elif icon_action == "library":
                    db.execute("UPDATE link_groups SET icon_stored_name=? WHERE id=?", (icon_stored, int(rid)))
                continue

            if apply_group and next_order is not None:
                db.execute(
                    "UPDATE links SET group_id=?, sub_order=NULL, display_order=? WHERE id=?",
                    (group_id, next_order, int(rid)),
                )
                next_order += 1

            if icon_action in {"clear", "upload", "library"}:
                if icon_action == "clear":
                    db.execute("UPDATE links SET icon_stored_name=NULL WHERE id=?", (int(rid),))
                else:
                    db.execute("UPDATE links SET icon_stored_name=? WHERE id=?", (icon_stored, int(rid)))

        db.commit()
        return redirect(url_for("content_edit"))

    @app.route("/content/icons/upload", methods=["POST"], endpoint="content_icons_upload")
    def content_icons_upload():
        gate = _require_write_access(url_for("content_edit"))
        if gate:
            return gate

        for f in request.files.getlist("icons"):
            if f and getattr(f, "filename", ""):
                _save_icon_upload(f)
        return redirect(url_for("content_edit"))

    @app.route("/content/icons/delete", methods=["POST"], endpoint="content_icons_delete")
    def content_icons_delete():
        gate = _require_write_access(url_for("content_edit"))
        if gate:
            return gate

        db = get_db()
        selected = []
        for raw in request.form.getlist("icons"):
            rel = _icon_choice_allowed(raw, db)
            if rel and rel not in selected:
                selected.append(rel)

        if not selected:
            return redirect(url_for("content_edit"))

        for rel in selected:
            db.execute(
                "UPDATE links SET icon_stored_name=NULL WHERE icon_stored_name=?",
                (rel,),
            )
            db.execute(
                "UPDATE link_groups SET icon_stored_name=NULL WHERE icon_stored_name=?",
                (rel,),
            )
        db.commit()

        for rel in selected:
            _unlink_upload(rel)

        return redirect(url_for("content_edit"))

    @app.route("/content/groups/rename/<int:group_id>", methods=["POST"])
    def links_group_rename(group_id: int):
        gate = _require_write_access(url_for("content_edit"))
        if gate:
            return gate

        name = (request.form.get("name") or "").strip()
        clear_icon = (request.form.get("clear_icon") or "").strip() == "1"
        icon_file = request.files.get("group_icon_file")

        if not name:
            return redirect(url_for("content_edit"))

        db = get_db()
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

        return redirect(url_for("content_edit"))

    @app.route("/content/groups/delete/<int:group_id>", methods=["POST"])
    def links_group_delete(group_id: int):
        gate = _require_write_access(url_for("content_edit"))
        if gate:
            return gate
        db = get_db()
        delete_group_contents = (request.form.get("delete_group_contents") or "").strip() == "1"
        _delete_group_record(db, int(group_id), delete_group_contents)
        db.commit()

        return redirect(url_for("content_edit"))

    @app.route("/content/delete/<int:link_id>", methods=["POST"])
    def links_delete(link_id: int):
        gate = _require_write_access(url_for("content_edit"))
        if gate:
            return gate

        db = get_db()
        row = db.execute("SELECT id FROM links WHERE id=?", (link_id,)).fetchone()
        if not row:
            abort(404)
        _delete_link_record(db, int(link_id))
        db.commit()

        return redirect(url_for("content_edit"))
