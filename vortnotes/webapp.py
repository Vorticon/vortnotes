import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, g, has_request_context, redirect, request, send_file, session, url_for
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix

from .db import connect as db_connect
from .deployment import direct_https_config, truthy_env
from .errors import register_error_handlers
from .routes.content import register_content_routes
from .routes.db_manage import register_db_manage_routes
from .routes.media import register_media_routes
from .routes.notes import register_note_routes
from .routes.settings_page import register_settings_routes
from .routes.uploads import register_upload_routes
from .settings import (
    ATTACHMENT_MAX_MB,
    BASE_DIR,
    CONFIG_PATH,
    DATA_DIR,
    DB_DIR,
    LEGACY_DB,
    MAX_CONTENT_LENGTH_MB,
    SECRET_KEY_PATH,
    UPLOAD_DIR,
)
from .storage import save_with_size_limit as _save_with_size_limit
from .storage import unique_store_name


def load_config():
    """Load app config (default DB name)."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "default_db": "notes.db",
        "secret_key": None,
        "admin_pwd_salt": None,
        "admin_pwd_hash": None,
        "db_appearance": {},
    }


def legacy_db_upload_key(db_name: str) -> str:
    """Legacy deterministic key (based on DB name).

    v22 originally derived the uploads folder from the database filename. That breaks
    when the DB is renamed. We keep this purely for *initial migration* so existing
    installs continue to find their current uploads folder the first time they run
    with the new, stable key system.
    """
    base = _normalize_db_name(db_name)
    stem = Path(base).stem
    # keep it filesystem-safe and short
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem)[:40] or "db"
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]
    return f"{stem}_{h}"


# Cache per-database upload keys to avoid opening SQLite on every request/upload.
# (This noticeably speeds up large multi-file uploads.)
_UPLOAD_KEY_CACHE: dict[str, str] = {}


def _db_cache_key(db_path: Path) -> str:
    try:
        return str(db_path.resolve())
    except Exception:
        return str(db_path)


def get_db_upload_key(db_path: Path) -> str:
    """Return the per-database uploads key stored inside the DB.

    - Stable across DB renames (key is stored in db_meta)
    - For older DBs that don't have upload_key yet, we seed it using the legacy
      deterministic key so existing uploads folders keep working with no manual steps.
    """
    ck = _db_cache_key(db_path)
    cached = _UPLOAD_KEY_CACHE.get(ck)
    if cached:
        return cached

    ensure_db_initialized(db_path)
    conn = db_connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    key = _get_db_meta(conn, "upload_key")
    if not key:
        key = legacy_db_upload_key(db_path.name)
        _set_db_meta(conn, "upload_key", key)
        conn.commit()
    conn.close()
    _UPLOAD_KEY_CACHE[ck] = key
    return key


def set_db_upload_key(db_path: Path, key: str) -> None:
    ensure_db_initialized(db_path)
    conn = db_connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    _set_db_meta(conn, "upload_key", key)
    conn.commit()
    conn.close()
    _UPLOAD_KEY_CACHE[_db_cache_key(db_path)] = key


def db_upload_key(db_name: str) -> str:
    """Stable key used to namespace uploads per DB.

    This is now stored *inside* the DB so renames don't break attachments.
    """
    db_path = resolve_db_path(_normalize_db_name(db_name))
    return get_db_upload_key(db_path)


def upload_dir_for_db(db_name: str) -> Path:
    d = UPLOAD_DIR / db_upload_key(db_name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _upload_filename_for_db(stored_name: str, db_name: str | None = None) -> str:
    """Build the URL path for an uploaded file within a DB's uploads namespace.

    Files are stored under:
        uploads/<db_upload_key>/<stored_name>

    The /uploads/<path:filename> route serves from UPLOAD_DIR, so we must include
    the db_upload_key prefix when generating URLs.
    """
    if not stored_name:
        return ""
    # Avoid accidental absolute paths.
    stored_name = str(stored_name).lstrip("/\\")
    use_db = db_name or selected_db_name()
    return f"{db_upload_key(use_db)}/{stored_name}"


def current_upload_dir() -> Path:
    return upload_dir_for_db(selected_db_name())


app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))

# Trust forwarded scheme/host information only when explicitly enabled. Enable
# this when exactly one trusted reverse proxy sits in front of VortNotes.
if truthy_env("VORTNOTES_TRUST_PROXY_HEADERS"):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# Lightweight health endpoints (safe for internal monitoring)
try:
    from .blueprints.health import bp as health_bp

    app.register_blueprint(health_bp)
except Exception:
    # Health endpoints are optional; do not block app start.
    pass

register_error_handlers(app)

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)


def _load_or_create_secret_key() -> str:
    """Return a stable Flask secret key.

    A stable key is required so sessions remain valid across restarts and
    security features like CSRF tokens work reliably.
    """
    try:
        if SECRET_KEY_PATH.exists():
            key = SECRET_KEY_PATH.read_text(encoding="utf-8").strip()
            if key:
                return key
        key = secrets.token_urlsafe(64)
        SECRET_KEY_PATH.write_text(key, encoding="utf-8")
        return key
    except Exception:
        # Fall back to an in-memory key (sessions reset on restart)
        return secrets.token_urlsafe(64)


app.secret_key = _load_or_create_secret_key()

# Safer cookie defaults (can be overridden by a reverse proxy terminating TLS)
app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")


@app.before_request
def _ensure_request_id():
    """Attach a simple request id for correlating logs."""
    try:
        rid = request.headers.get("X-Request-ID")
        if not rid:
            rid = uuid.uuid4().hex[:12]
        request.environ["REQUEST_ID"] = rid
    except Exception:
        pass


# --- Hot-reload global config (for multi-worker servers) ---
# Gunicorn/uwsgi workers do not share memory. If one worker updates CONFIG_PATH
# (e.g., via /db/config-set), other workers won't see new upload limits until
# restart unless we re-load them.
_LAST_CONFIG_MTIME: float | None = None


def _maybe_reload_global_config() -> None:
    """Reload global config-derived settings when CONFIG_PATH changes."""
    global _LAST_CONFIG_MTIME
    try:
        if not CONFIG_PATH.exists():
            return
        mtime = float(CONFIG_PATH.stat().st_mtime)
        if _LAST_CONFIG_MTIME is None:
            _LAST_CONFIG_MTIME = mtime
            return
        # If config changed on disk, re-apply upload limits for this worker.
        if mtime != _LAST_CONFIG_MTIME:
            _LAST_CONFIG_MTIME = mtime
            try:
                apply_upload_limits()
            except Exception:
                pass
    except Exception:
        pass


@app.before_request
def _reload_limits_if_config_changed():
    # Keep this early and lightweight; it fixes confusing intermittent 50MB 413s
    # when running multiple workers.
    _maybe_reload_global_config()


def _env_int(name: str, default: int) -> int:
    try:
        v = str(os.getenv(name, "")).strip()
        return int(v) if v else int(default)
    except Exception:
        return int(default)


def get_upload_limits_effective() -> dict:
    """Return effective upload limits.

    Limits can come from environment variables (highest priority) or from
    the persisted app config (global, not DB-specific).
    """
    cfg = load_config()
    ul = cfg.get("upload_limits")
    ul = ul if isinstance(ul, dict) else {}

    # Defaults
    default_max_request_mb = int(MAX_CONTENT_LENGTH_MB)
    # Per-file attachment/media limit (MB). Defaults to 50MB.
    default_attachment_mb = int(ATTACHMENT_MAX_MB)
    default_inline_mb = int(MAX_CONTENT_LENGTH_MB)

    # From config (if present)
    try:
        default_max_request_mb = int(ul.get("max_request_mb", default_max_request_mb))
    except Exception:
        pass
    try:
        if "attachment_max_mb" in ul:
            default_attachment_mb = int(ul.get("attachment_max_mb", default_attachment_mb))
        elif "attachment_max_gb" in ul:
            # Backward compatible persisted config
            default_attachment_mb = int(ul.get("attachment_max_gb", 0)) * 1024
    except Exception:
        pass
    try:
        default_inline_mb = int(ul.get("inline_media_max_mb", default_inline_mb))
    except Exception:
        pass

    # Env overrides (if set)
    env_override = False
    if str(os.getenv("VORTNOTES_MAX_CONTENT_LENGTH_MB", "")).strip():
        env_override = True
    if str(os.getenv("VORTNOTES_MAX_CONTENT_MB", "")).strip():
        env_override = True
    if str(os.getenv("VORTNOTES_ATTACHMENT_MAX_MB", "")).strip():
        env_override = True
    if str(os.getenv("VORTNOTES_ATTACHMENT_MAX_GB", "")).strip():
        env_override = True
    if str(os.getenv("VORTNOTES_INLINE_MEDIA_MAX_MB", "")).strip():
        env_override = True

    max_request_mb = _env_int("VORTNOTES_MAX_CONTENT_LENGTH_MB", default_max_request_mb)
    # Prefer MB env var; fall back to legacy GB only if it is explicitly set.
    if str(os.getenv("VORTNOTES_ATTACHMENT_MAX_MB", "")).strip():
        attachment_max_mb = _env_int("VORTNOTES_ATTACHMENT_MAX_MB", default_attachment_mb)
    elif str(os.getenv("VORTNOTES_ATTACHMENT_MAX_GB", "")).strip():
        attachment_max_mb = _env_int("VORTNOTES_ATTACHMENT_MAX_GB", max(0, int(default_attachment_mb // 1024))) * 1024
    else:
        # No env override: keep the config/default MB value (avoids rounding 50MB -> 0GB).
        attachment_max_mb = int(default_attachment_mb)
    inline_media_max_mb = _env_int("VORTNOTES_INLINE_MEDIA_MAX_MB", default_inline_mb)

    # Keep the global request cap >= per-file caps to avoid confusing 413 errors.
    try:
        max_request_mb = max(int(max_request_mb), int(attachment_max_mb), int(inline_media_max_mb))
    except Exception:
        pass

    # clamp
    max_request_mb = max(1, int(max_request_mb))
    attachment_max_mb = max(0, int(attachment_max_mb))
    inline_media_max_mb = max(1, int(inline_media_max_mb))

    return {
        "max_request_mb": max_request_mb,
        "attachment_max_mb": attachment_max_mb,
        "inline_media_max_mb": inline_media_max_mb,
        "env_override": env_override,
    }


def apply_upload_limits() -> None:
    """Apply effective limits to app.config for runtime use."""
    eff = get_upload_limits_effective()
    app.config["VN_ATTACHMENT_MAX_BYTES"] = int(eff["attachment_max_mb"]) * 1024 * 1024
    app.config["VN_INLINE_MEDIA_MAX_BYTES"] = int(eff["inline_media_max_mb"]) * 1024 * 1024

    # Backward compatible override: if VORTNOTES_MAX_CONTENT_MB is set, it becomes the global request limit.
    _legacy_mb = os.getenv("VORTNOTES_MAX_CONTENT_MB", "").strip()
    if _legacy_mb:
        try:
            app.config["MAX_CONTENT_LENGTH"] = int(_legacy_mb) * 1024 * 1024
        except Exception:
            app.config["MAX_CONTENT_LENGTH"] = app.config["VN_ATTACHMENT_MAX_BYTES"]
    else:
        app.config["MAX_CONTENT_LENGTH"] = int(eff["max_request_mb"]) * 1024 * 1024

    app.config["MAX_FORM_MEMORY_SIZE"] = app.config.get("MAX_CONTENT_LENGTH")

    # Keep back-compat module-level names in sync for any legacy references.
    try:
        globals()["ATTACHMENT_MAX_BYTES"] = int(app.config.get("VN_ATTACHMENT_MAX_BYTES") or 0)
        globals()["INLINE_MEDIA_MAX_BYTES"] = int(app.config.get("VN_INLINE_MEDIA_MAX_BYTES") or 0)
    except Exception:
        pass


def get_attachment_max_bytes() -> int:
    return int(app.config.get("VN_ATTACHMENT_MAX_BYTES") or 0) or (int(ATTACHMENT_MAX_MB) * 1024 * 1024)


def get_inline_media_max_bytes() -> int:
    return int(app.config.get("VN_INLINE_MEDIA_MAX_BYTES") or 0) or (int(MAX_CONTENT_LENGTH_MB) * 1024 * 1024)


# Upload size limits (initialized from env/config at startup)
apply_upload_limits()

# Back-compat module-level names (some older code may still reference these)
ATTACHMENT_MAX_BYTES = get_attachment_max_bytes()
INLINE_MEDIA_MAX_BYTES = get_inline_media_max_bytes()


# If you terminate TLS upstream (reverse proxy), you can force Secure cookies.
# Set VORTNOTES_FORCE_SECURE_COOKIES=1 to always mark session cookies as Secure.
if truthy_env("VORTNOTES_FORCE_SECURE_COOKIES") or bool(direct_https_config(CONFIG_PATH)["enabled"]):
    app.config["SESSION_COOKIE_SECURE"] = True


# -------------------- CSRF protection --------------------
def _csrf_token() -> str:
    tok = session.get("_csrf_token")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf_token"] = tok
    return tok


def _csrf_field() -> Markup:
    return Markup(f'<input type="hidden" name="csrf_token" value="{_csrf_token()}">')


@app.context_processor
def _inject_csrf():
    return {"csrf_token": _csrf_token, "csrf_field": _csrf_field}


@app.before_request
def _csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # Allow static files, though they should never be unsafe methods.
        if request.endpoint == "static":
            return None

        sent = (
            request.form.get("csrf_token") or request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token")
        )
        if not sent or sent != session.get("_csrf_token"):
            return ("CSRF validation failed. Please refresh and try again.", 400)
    return None


@app.after_request
def _set_security_headers(resp):
    # HSTS only when the request is served over HTTPS.
    try:
        if request.is_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=15552000; includeSubDomains")
    except Exception:
        pass
    # Reasonable hardening for a self-hosted app.
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    # Allow cross-origin iframes (e.g., YouTube embeds) to receive a minimal referrer
    # so the embedded player can initialize properly. This keeps full-path referrers
    # for same-origin, and only sends the origin for cross-origin.
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    resp.headers.setdefault("X-Download-Options", "noopen")
    # Echo request id for easier debugging.
    try:
        rid = request.environ.get("REQUEST_ID")
        if rid:
            resp.headers.setdefault("X-Request-ID", rid)
    except Exception:
        pass
    # Allow inline styles/scripts because templates currently use small inline blocks.
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; form-action 'self'; object-src 'none'; "
        "img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; frame-src 'self' https://www.youtube.com https://www.youtube-nocookie.com https:; "
        "media-src 'self' https:; frame-ancestors 'self';",
    )
    # Echo request id (useful when reporting issues)
    try:
        rid = request.environ.get("REQUEST_ID")
        if rid:
            resp.headers.setdefault("X-Request-ID", rid)
    except Exception:
        pass
    return resp


"""Main Flask web application.

This file is still large due to legacy routes, but we are gradually extracting
subsystems into smaller modules (e.g. uploads routes and sanitizer).
"""


def _normalize_db_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "notes.db"
    if not name.endswith(".db"):
        name += ".db"
    return name


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ---- Per-database appearance (theme + background) ----

THEME_PRESETS = {
    # preset_name: (accent, accent2)
    "purple-teal": ("#7c5cff", "#2dd4bf"),
    "blue-orange": ("#3b82f6", "#f97316"),
    "red-gold": ("#ef4444", "#f59e0b"),
    "green-lime": ("#22c55e", "#a3e635"),
    "mono": ("#cbd5e1", "#94a3b8"),
}

ALLOWED_BG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# Attachments: allow common safe document/media formats; block executable/script types.
ATTACHMENT_DENY_EXTS = {
    ".exe",
    ".dll",
    ".bat",
    ".cmd",
    ".com",
    ".msi",
    ".ps1",
    ".vbs",
    ".js",
    ".mjs",
    ".jar",
    ".sh",
    ".py",
    ".pyc",
    ".php",
    ".pl",
    ".rb",
    ".go",
    ".rs",
    ".c",
    ".cpp",
    ".html",
    ".htm",
    ".svg",
    ".xhtml",
    ".xml",
}
# Everything else is permitted by default; you can tighten this by setting VORTNOTES_ATTACHMENT_ALLOWLIST
# to a comma-separated list (e.g. ".png,.jpg,.pdf,.txt,.md").


def _attachment_ext_allowed(filename: str) -> bool:
    ext = (Path(filename).suffix or "").lower()
    if not ext:
        return False
    allow = os.getenv("VORTNOTES_ATTACHMENT_ALLOWLIST", "").strip()
    if allow:
        allowed = {e.strip().lower() for e in allow.split(",") if e.strip()}
        return ext in allowed
    return ext not in ATTACHMENT_DENY_EXTS


def _get_db_appearance_map(cfg: dict) -> dict:
    m = cfg.get("db_appearance")
    return m if isinstance(m, dict) else {}


def get_db_appearance(db_name: str) -> dict:
    """Return appearance dict for a DB: {preset, accent, accent2, bg_filename}."""
    cfg = load_config()
    m = _get_db_appearance_map(cfg)
    return m.get(_normalize_db_name(db_name), {}) if isinstance(m.get(_normalize_db_name(db_name), {}), dict) else {}


def set_db_appearance(db_name: str, appearance: dict):
    cfg = load_config()
    m = _get_db_appearance_map(cfg)
    m[_normalize_db_name(db_name)] = appearance
    cfg["db_appearance"] = m
    save_config(cfg)


def clear_db_appearance(db_name: str):
    cfg = load_config()
    m = _get_db_appearance_map(cfg)
    m.pop(_normalize_db_name(db_name), None)
    cfg["db_appearance"] = m
    save_config(cfg)


# ---- Per-database meta / permissions ----


def get_db_last_access(db_name: str) -> str:
    """Return ISO timestamp string for when a DB was last accessed (or empty)."""
    a = get_db_appearance(db_name) or {}
    v = a.get("last_access") or ""
    return v if isinstance(v, str) else ""


def touch_db_last_access(db_name: str) -> None:
    """Update last_access timestamp for a DB (stored in db_appearance)."""
    db_name = _normalize_db_name(db_name)
    a = get_db_appearance(db_name) or {}
    if not isinstance(a, dict):
        a = {}
    a["last_access"] = datetime.now().isoformat(timespec="seconds")
    set_db_appearance(db_name, a)


def get_db_read_without_password(db_name: str) -> bool:
    """If True, allow read-only access to a password-protected DB without unlocking."""
    a = get_db_appearance(db_name) or {}
    try:
        return bool(int(a.get("perm_read_no_password") or 0))
    except Exception:
        return False


def set_db_read_without_password(db_name: str, enabled: bool) -> None:
    db_name = _normalize_db_name(db_name)
    a = get_db_appearance(db_name) or {}
    if not isinstance(a, dict):
        a = {}
    a["perm_read_no_password"] = 1 if enabled else 0
    set_db_appearance(db_name, a)


# ---- Appearance helpers for DB operations ----
def copy_db_appearance(src_db: str, dest_db: str):
    """Copy per-db appearance config from src_db to dest_db (if any)."""
    try:
        src_db = _normalize_db_name(src_db)
        dest_db = _normalize_db_name(dest_db)
        cfg = load_config()
        amap = _get_db_appearance_map(cfg)
        src = amap.get(src_db)
        if src is not None:
            amap[dest_db] = dict(src)
            save_config(cfg)
    except Exception:
        pass


def rename_db_appearance(old_db: str, new_db: str):
    """Move per-db appearance config entry when a DB file is renamed."""
    try:
        old_db = _normalize_db_name(old_db)
        new_db = _normalize_db_name(new_db)
        cfg = load_config()
        amap = _get_db_appearance_map(cfg)
        if old_db in amap:
            amap[new_db] = amap.pop(old_db)
            save_config(cfg)
    except Exception:
        pass


def delete_db_appearance(db_name: str):
    """Remove per-db appearance config entry (used when deleting a DB)."""
    try:
        db_name = _normalize_db_name(db_name)
        cfg = load_config()
        amap = _get_db_appearance_map(cfg)
        if db_name in amap:
            amap.pop(db_name, None)
            save_config(cfg)
    except Exception:
        pass


def _safe_bg_filename(db_name: str, ext: str) -> str:
    ext = (ext or "").lower()
    if ext not in ALLOWED_BG_EXTS:
        ext = ".png"
    return f"_db_background{ext}"


def list_db_files():
    # only *.db in DB_DIR plus legacy notes.db if present
    files = [p.name for p in sorted(DB_DIR.glob("*.db"))]
    if LEGACY_DB.exists() and LEGACY_DB.name not in files:
        files.insert(0, LEGACY_DB.name)
    return files


def resolve_db_path(db_name: str) -> Path:
    if db_name == LEGACY_DB.name and LEGACY_DB.exists():
        return LEGACY_DB
    return DB_DIR / db_name


def selected_db_name() -> str:
    """DB selection is per-browser via cookie.

    Falls back to config.json's default_db if cookie isn't set.
    """
    cfg = load_config()
    cookie_name = None
    if has_request_context():
        cookie_name = request.cookies.get("selected_db")
    return _normalize_db_name(cookie_name or cfg.get("default_db") or "notes.db")


def current_db_path() -> Path:
    name = selected_db_name()
    # ensure exists in DB_DIR or legacy
    p = resolve_db_path(name)
    if not p.exists():
        # create in DB_DIR by default
        p = DB_DIR / name
    return p


def ensure_db_initialized(db_path: Path):
    # ensure folders exist
    db_path.parent.mkdir(exist_ok=True)
    backup_existing = db_path.exists() and db_path.stat().st_size > 0
    db = db_connect(db_path)
    db.execute("PRAGMA foreign_keys = ON;")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            tag TEXT,
            created_at TEXT NOT NULL,
            content_html TEXT NOT NULL,
            -- Quill Delta JSON (reliable round-tripping for tables and other rich formats).
            content_delta TEXT
        )
    """
    )

    # Notes table migration: add 'updated_at' and 'pinned' columns (newer versions).
    try:
        cols = [r[1] for r in db.execute("PRAGMA table_info(notes)").fetchall()]
        if "updated_at" not in cols:
            db.execute("ALTER TABLE notes ADD COLUMN updated_at TEXT")
            db.execute("UPDATE notes SET updated_at = created_at WHERE updated_at IS NULL OR updated_at = ''")
        if "pinned" not in cols:
            db.execute("ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        if "content_delta" not in cols:
            db.execute("ALTER TABLE notes ADD COLUMN content_delta TEXT")
    except Exception:
        pass

    # Optional full-text index (FTS5). If FTS5 isn't available in the
    # environment, we silently fall back to LIKE-based searching.
    try:
        db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
            "title, tag, content_html, content='notes', content_rowid='id')"
        )
        db.execute(
            "CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN "
            "INSERT INTO notes_fts(rowid, title, tag, content_html) VALUES (new.id, new.title, new.tag, new.content_html); "
            "END;"
        )
        db.execute(
            "CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN "
            "INSERT INTO notes_fts(notes_fts, rowid, title, tag, content_html) VALUES('delete', old.id, old.title, old.tag, old.content_html); "
            "END;"
        )
        db.execute(
            "CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN "
            "INSERT INTO notes_fts(notes_fts, rowid, title, tag, content_html) VALUES('delete', old.id, old.title, old.tag, old.content_html); "
            "INSERT INTO notes_fts(rowid, title, tag, content_html) VALUES (new.id, new.title, new.tag, new.content_html); "
            "END;"
        )
        # Ensure the FTS index is populated for existing rows.
        db.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")
    except Exception:
        pass
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS db_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id INTEGER NOT NULL,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            display_order INTEGER NOT NULL DEFAULT 0,
            icon_stored_name TEXT,
            width INTEGER,
            height INTEGER,
            FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
        )
    """
    )
    # lightweight migration for old DBs
    for col_sql in [
        "ALTER TABLE attachments ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE attachments ADD COLUMN icon_stored_name TEXT",
        "ALTER TABLE attachments ADD COLUMN width INTEGER",
        "ALTER TABLE attachments ADD COLUMN height INTEGER",
    ]:
        try:
            db.execute(col_sql)
        except sqlite3.OperationalError:
            pass

    # These cover attachment counts and ordered attachment retrieval on note pages.
    db.execute("CREATE INDEX IF NOT EXISTS idx_attachments_note_id ON attachments(note_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_attachments_note_order " "ON attachments(note_id, display_order, id)")

    # Notes table migration: rename 'description' -> 'title' (keep old column if present).
    try:
        cols = [r[1] for r in db.execute("PRAGMA table_info(notes)").fetchall()]
        if "title" not in cols:
            db.execute("ALTER TABLE notes ADD COLUMN title TEXT")
            if "description" in cols:
                db.execute("UPDATE notes SET title = COALESCE(NULLIF(title,''), description)")
    except Exception:
        pass

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS link_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            icon_stored_name TEXT,
            created_at TEXT NOT NULL,
            display_order INTEGER NOT NULL DEFAULT 0
        )
    """
    )
    # lightweight migration for old DBs
    for col_sql in [
        "ALTER TABLE link_groups ADD COLUMN icon_stored_name TEXT",
        "ALTER TABLE link_groups ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0",
    ]:
        try:
            db.execute(col_sql)
        except Exception:
            pass

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            group_id INTEGER,
            target TEXT NOT NULL DEFAULT '_blank',
            embed INTEGER NOT NULL DEFAULT 0,
            item_kind TEXT NOT NULL DEFAULT 'link',
            icon_stored_name TEXT,
            file_stored_name TEXT,
            file_original_name TEXT,
            file_mime TEXT,
            file_size INTEGER,
            ha_entity_id TEXT,
            ha_entity_type TEXT,
            created_at TEXT NOT NULL,
            display_order INTEGER NOT NULL DEFAULT 0,
            sub_order INTEGER
        )
    """
    )
    # lightweight migration for old DBs
    for col_sql in [
        "ALTER TABLE links ADD COLUMN icon_stored_name TEXT",
        "ALTER TABLE links ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE links ADD COLUMN target TEXT NOT NULL DEFAULT '_blank'",
        "ALTER TABLE links ADD COLUMN group_id INTEGER",
        "ALTER TABLE links ADD COLUMN sub_order INTEGER",
        "ALTER TABLE links ADD COLUMN embed INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE links ADD COLUMN item_kind TEXT NOT NULL DEFAULT 'link'",
        "ALTER TABLE links ADD COLUMN file_stored_name TEXT",
        "ALTER TABLE links ADD COLUMN file_original_name TEXT",
        "ALTER TABLE links ADD COLUMN file_mime TEXT",
        "ALTER TABLE links ADD COLUMN file_size INTEGER",
        "ALTER TABLE links ADD COLUMN ha_entity_id TEXT",
        "ALTER TABLE links ADD COLUMN ha_entity_type TEXT",
    ]:
        try:
            db.execute(col_sql)
        except Exception:
            pass

    # Media library (database-scoped uploads)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            mime TEXT NOT NULL,
            created_at TEXT NOT NULL,
            display_order INTEGER NOT NULL DEFAULT 0
        )
    """
    )
    for col_sql in [
        "ALTER TABLE media ADD COLUMN mime TEXT NOT NULL DEFAULT 'application/octet-stream'",
        "ALTER TABLE media ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0",
    ]:
        try:
            db.execute(col_sql)
        except Exception:
            pass

    from .migrations import apply_migrations

    # Persist the legacy-compatible bootstrap before SQLite's online backup API
    # snapshots an existing database for a pending versioned migration.
    db.commit()
    apply_migrations(
        db,
        db_path=db_path,
        backup_dir=DATA_DIR / "backups" / "schema",
        backup_existing=backup_existing,
    )

    db.commit()
    db.close()


def _table_exists(conn, table: str) -> bool:
    """Return True if the given table exists in the connected SQLite DB."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _table_has_column(conn, table: str, col: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == col for r in rows)
    except Exception:
        return False


def _title_select_expr(conn) -> str:
    """Return SQL expression to read the note title across schema versions."""
    return "COALESCE(title, description)" if _table_has_column(conn, "notes", "description") else "title"


def _get_db_meta(conn, key: str):
    row = conn.execute("SELECT value FROM db_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _set_db_meta(conn, key: str, value: str):
    conn.execute(
        "INSERT INTO db_meta(key,value) VALUES(?,?) " "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_db_password_info(db_path: Path):
    conn = db_connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    ensure_db_initialized(db_path)
    salt = _get_db_meta(conn, "pwd_salt")
    phash = _get_db_meta(conn, "pwd_hash")
    conn.close()
    return salt, phash


def set_db_password(db_path: Path, password: str):
    salt = secrets.token_hex(16)
    phash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
    conn = db_connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    ensure_db_initialized(db_path)
    _set_db_meta(conn, "pwd_salt", salt)
    _set_db_meta(conn, "pwd_hash", phash)
    conn.commit()
    conn.close()


def clear_db_password(db_path: Path):
    conn = db_connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    ensure_db_initialized(db_path)
    conn.execute("DELETE FROM db_meta WHERE key IN ('pwd_salt','pwd_hash')")
    conn.commit()
    conn.close()


def verify_db_password(db_path: Path, password: str) -> bool:
    salt, phash = get_db_password_info(db_path)
    if not salt or not phash:
        return True
    cand = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
    return hmac.compare_digest(cand, phash)


def _db_has_password(db_path: Path) -> bool:
    salt, phash = get_db_password_info(db_path)
    return bool(salt and phash)


# NOTE: DB passwords only protect opening/using a DB. Database Management actions
# are controlled via the Admin Password feature (see /db/admin-login).


def admin_password_is_set() -> bool:
    cfg = load_config()
    return bool(cfg.get("admin_pwd_salt") and cfg.get("admin_pwd_hash"))


def set_admin_password(password: str):
    cfg = load_config()
    salt = secrets.token_hex(16)
    phash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
    cfg["admin_pwd_salt"] = salt
    cfg["admin_pwd_hash"] = phash
    save_config(cfg)


def bootstrap_admin_password_from_env() -> bool:
    """Seed the admin password once from VORTNOTES_ADMIN_PASSWORD."""
    if admin_password_is_set():
        return False
    password = os.getenv("VORTNOTES_ADMIN_PASSWORD", "").strip()
    if not password:
        return False
    set_admin_password(password)
    return True


def clear_admin_password():
    cfg = load_config()
    cfg.pop("admin_pwd_salt", None)
    cfg.pop("admin_pwd_hash", None)
    save_config(cfg)


def verify_admin_password(password: str) -> bool:
    cfg = load_config()
    salt = cfg.get("admin_pwd_salt")
    phash = cfg.get("admin_pwd_hash")
    if not salt or not phash:
        return True
    cand = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
    return hmac.compare_digest(cand, phash)


def _is_admin_authed() -> bool:
    return bool(session.get("admin_authed"))


def _set_admin_authed(remember: bool):
    session["admin_authed"] = True
    session.permanent = bool(remember)


def _current_db_name():
    # keep helper name for template injection / legacy uses
    return selected_db_name()


def _has_db_session_access(name: str) -> bool:
    """Return whether this browser has a direct session for one database."""
    overrides = session.get("db_logout_overrides", {})
    if isinstance(overrides, dict) and overrides.get(name):
        return False
    if session.get("remember_all_dbs"):
        return True
    unlocked = session.get("unlocked_dbs", {})
    return bool(isinstance(unlocked, dict) and unlocked.get(name))


def _is_unlocked(name: str) -> bool:
    # If the user is logged in as admin, treat all DBs as unlocked.
    # This is useful for admins managing multiple protected databases.
    if _is_admin_authed():
        return True
    return _has_db_session_access(name)


def _set_unlocked(name: str, remember: bool):
    unlocked = session.get("unlocked_dbs", {})
    unlocked[name] = True
    session["unlocked_dbs"] = unlocked
    overrides = session.get("db_logout_overrides", {})
    if isinstance(overrides, dict):
        overrides.pop(name, None)
        session["db_logout_overrides"] = overrides
    if remember:
        # A remembered DB login establishes one trusted browser session. We do
        # not store any database password; logout revokes the session marker.
        session["remember_all_dbs"] = True
        session.permanent = True
    elif not session.get("remember_all_dbs"):
        session.permanent = False


def get_db():
    if "db" not in g:
        db_path = current_db_path()
        ensure_db_initialized(db_path)
        g.db = db_connect(db_path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON;")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def iso_now():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def normalize_tags(tag_input: str) -> str:
    """Normalize comma-separated tags into a stable, de-duplicated string."""
    parts = [p.strip() for p in (tag_input or "").split(",")]
    parts = [p for p in parts if p]
    seen = set()
    out = []
    for p in parts:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return ", ".join(out)


def fmt_dt(iso_str: str) -> str:
    """Render ISO string like '2025-12-22T03:41:10Z' as '2025-12-22 03:41'."""
    if not iso_str:
        return ""
    s = iso_str.strip()
    if s.endswith("Z"):
        s = s[:-1]
    try:
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        # fall back: trim seconds + Z
        return iso_str.replace("T", " ").replace("Z", "")[:16]


@app.template_filter("fmt_dt")
def _fmt_dt_filter(value):
    return fmt_dt(value)


@app.context_processor
def inject_current_db():
    name = _current_db_name()
    # Used by base.html to optionally prompt for DB password on the main screen.
    needs_unlock = False
    try:
        db_path = resolve_db_path(name)
        ensure_db_initialized(db_path)
        salt, phash = get_db_password_info(db_path)
        needs_unlock = bool(salt and phash and not _is_unlocked(name))
    except Exception:
        needs_unlock = False

    # Provide DB list/selection to all templates (for navbar selector).
    try:
        dbs = list_db_files()
    except Exception:
        dbs = []
    # Per-DB appearance (theme + background)
    appearance = get_db_appearance(name) or {}
    preset = (appearance.get("preset") or "").strip().lower()
    accent = None
    accent2 = None
    if preset and preset != "default":
        if preset in THEME_PRESETS:
            accent, accent2 = THEME_PRESETS[preset]
    elif not preset:
        accent = (appearance.get("accent") or "").strip() or None
        accent2 = (appearance.get("accent2") or "").strip() or None
    bg_filename = (appearance.get("bg_filename") or "").strip()
    # Optional toggles
    try:
        btn_hover_outline = int(appearance.get("btn_hover_outline") or 0)
    except Exception:
        btn_hover_outline = 0
    btn_hover_outline = 1 if btn_hover_outline else 0
    # Background brightness (0-100). Higher means brighter image (less dark overlay).
    try:
        bg_brightness = int(appearance.get("bg_brightness") or 10)
    except Exception:
        bg_brightness = 10
    bg_brightness = max(0, min(100, bg_brightness))
    # Convert brightness to an overlay alpha used by CSS.
    # brightness=0  -> overlay ~0.92 (dark)
    # brightness=100-> overlay ~0.30 (bright)
    bg_overlay = 0.92 - (bg_brightness / 100.0) * 0.62
    bg_overlay = max(0.30, min(0.92, bg_overlay))
    bg_url = None
    if bg_filename:
        try:
            bg_path = upload_dir_for_db(name) / bg_filename
            if bg_path.exists():
                bg_url = url_for("uploaded_file", filename=f"{db_upload_key(name)}/{bg_filename}")
        except Exception:
            bg_url = None

    return {
        "current_db_name": name,
        "selected_db": name,
        "db_key": db_upload_key(name),
        "upload_prefix": f"/uploads/{db_upload_key(name)}",
        "db_theme_accent": accent,
        "db_theme_accent2": accent2,
        "db_theme_bg_url": bg_url,
        "db_theme_bg_overlay": f"{bg_overlay:.2f}",
        "db_btn_hover_outline": btn_hover_outline,
        "dbs": dbs,
        "admin_password_is_set": admin_password_is_set(),
        "admin_authed": _is_admin_authed(),
        "db_requires_unlock": needs_unlock,
    }


def _parse_index_filters(args) -> list:
    """Parse filters from query args.

    Expected: repeated f_field / f_value params.
    """
    fields = args.getlist("f_field")
    values = args.getlist("f_value")
    out = []
    for f, v in zip(fields, values):
        f = (f or "all").strip().lower()
        v = (v or "").strip()
        if not v:
            continue
        if f not in {"all", "title", "tag", "date"}:
            f = "all"
        out.append({"field": f, "value": v})
    return out


def _notes_where_clause(conn, filters: list):
    """Return (where_sql, params) for the notes table based on filters."""
    title_expr = _title_select_expr(conn)
    has_fts = _table_exists(conn, "notes_fts")

    def _fts_match_query(field: str, value: str) -> str | None:
        # Keep this intentionally conservative to avoid unexpected FTS syntax.
        tokens = re.findall(r"[A-Za-z0-9]+", value or "")
        if not tokens:
            return None
        q = " ".join(tokens)
        if field == "title":
            return f"title:{q}"
        if field == "tag":
            return f"tag:{q}"
        return q

    clauses = []
    params = []
    for f in filters or []:
        field = (f.get("field") or "all").lower()
        val = (f.get("value") or "").strip()
        if not val:
            continue
        like = f"%{val}%"
        if field == "date":
            # created_at is stored as ISO string; substring matching works well.
            clauses.append("(created_at LIKE ?)")
            params.append(like)
        elif has_fts and field in {"all", "title", "tag"}:
            q = _fts_match_query(field if field != "all" else "", val)
            if q:
                clauses.append("(id IN (SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?))")
                params.append(q)
            else:
                # fall back to LIKE if we can't form a safe FTS query
                if field == "title":
                    clauses.append(f"({title_expr} LIKE ?)")
                    params.append(like)
                elif field == "tag":
                    clauses.append("(tag LIKE ?)")
                    params.append(like)
                else:
                    clauses.append(f"(({title_expr} LIKE ?) OR (tag LIKE ?) OR (created_at LIKE ?))")
                    params.extend([like, like, like])
        elif field == "title":
            clauses.append(f"({title_expr} LIKE ?)")
            params.append(like)
        elif field == "tag":
            clauses.append("(tag LIKE ?)")
            params.append(like)
        else:
            clauses.append(f"(({title_expr} LIKE ?) OR (tag LIKE ?) OR (created_at LIKE ?))")
            params.extend([like, like, like])

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


@app.template_global()
def index_url(page: int = 1, filters: list | None = None, sort: str | None = None, direction: str | None = None):
    """Build an index URL that preserves filters + sort."""
    # Defaults from the current request (if any).
    if sort is None and has_request_context():
        sort = request.args.get("sort")
    if direction is None and has_request_context():
        direction = request.args.get("dir")

    sort = (sort or "id").strip().lower()
    direction = (direction or "desc").strip().lower()

    allowed = {"id", "title", "tag", "date"}
    if sort not in allowed:
        sort = "id"
    if direction not in {"asc", "desc"}:
        direction = "desc"

    params = [("page", str(max(1, int(page or 1))))]
    for f in filters or []:
        params.append(("f_field", (f.get("field") or "all")))
        params.append(("f_value", (f.get("value") or "")))
    params.append(("sort", sort))
    params.append(("dir", direction))
    return url_for("index") + ("?" + urlencode(params) if params else "")


def is_image_filename(name: str) -> bool:
    ext = (Path(name).suffix or "").lower()
    return ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


def init_db():
    # Ensure default DB exists and is migrated
    ensure_db_initialized(current_db_path())


def next_attachment_order(db, note_id: int) -> int:
    row = db.execute(
        "SELECT COALESCE(MAX(display_order), -1) AS m FROM attachments WHERE note_id=?", (note_id,)
    ).fetchone()
    return int(row["m"]) + 1


@app.before_request
def require_db_unlocked():
    p = request.path or ""
    if p.startswith("/static/") or p.startswith("/uploads/"):
        return
    if p == "/healthz":
        return
    # Allow switching DBs from the locked home screen.
    # If the currently-selected DB is locked, we still need to allow the POST
    # that changes the selected_db cookie; otherwise this request would be
    # redirected to /db/select for the *old* DB and the selection would never
    # take effect.
    if p == "/select-db":
        return
    # Never require a DB password to access Database Management or the unlock screen.
    # DB passwords protect opening/using a DB for notes pages.
    if p.startswith("/db"):
        return

    # Settings is a navigation hub and must remain accessible even when the
    # selected DB is locked.
    if p == "/settings" or p.startswith("/settings/"):
        return
    # The main screen should redirect to /db/select when the selected DB is locked.
    name = _current_db_name()
    db_path = resolve_db_path(name)
    ensure_db_initialized(db_path)
    salt, phash = get_db_password_info(db_path)
    if salt and phash and not _is_unlocked(name):
        # Optional: allow read-only access without unlocking.
        if get_db_read_without_password(name) and request.method == "GET":
            pth = request.path or ""
            if (
                pth == "/"
                or re.match(r"^/notes/\d+/?$", pth)
                # Content (new unified page) + legacy links route
                or pth == "/content"
                or pth == "/content/"
                or pth.startswith("/content/apps/")
                or re.match(r"^/content/group/\d+/items/?$", pth)
                or pth == "/links"
                or pth == "/links/"
                or pth == "/media"
                or pth == "/media/"
            ):
                return
        # Home Assistant tiles are intentionally usable in read-only mode.
        # They do not mutate the selected VortNotes database.
        if get_db_read_without_password(name) and request.method == "POST" and p == "/content/ha/activate":
            return
        return redirect(url_for("settings_page", name=name, next=request.full_path))


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


bootstrap_admin_password_from_env()

register_note_routes(app)
register_content_routes(app)
register_media_routes(app)
register_settings_routes(app)
register_db_manage_routes(app)

register_upload_routes(
    app,
    upload_root_dir=UPLOAD_DIR,
    inline_media_max_bytes_fn=get_inline_media_max_bytes,
    current_upload_dir=current_upload_dir,
    selected_db_name=selected_db_name,
    upload_relpath_for_db=_upload_filename_for_db,
    unique_store_name=unique_store_name,
    save_with_size_limit=_save_with_size_limit,
)

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
