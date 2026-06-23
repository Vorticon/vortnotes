"""Small helpers for local Home Assistant integration."""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


class HomeAssistantError(RuntimeError):
    """Raised when Home Assistant configuration or requests fail."""


def normalize_local_base_url(raw: str) -> str:
    """Return a normalized local Home Assistant base URL or an empty string."""
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

    host = (parsed.hostname or "").strip().lower()
    if not host or not _is_local_host(host):
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def _is_local_host(host: str) -> bool:
    if host in {"localhost", "homeassistant", "homeassistant.local"}:
        return True
    if host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        pass
    return "." not in host


def home_assistant_config(cfg: dict) -> dict:
    raw = cfg.get("home_assistant")
    if not isinstance(raw, dict):
        raw = {}
    base_url = normalize_local_base_url(str(raw.get("base_url") or ""))
    token = str(raw.get("token") or "").strip()
    return {
        "enabled": bool(raw.get("enabled")),
        "base_url": base_url,
        "token": token,
        "has_token": bool(token),
    }


def call_home_assistant(cfg: dict, path: str, method: str = "GET", payload: dict | None = None, timeout: int = 5):
    ha = home_assistant_config(cfg)
    if not ha["enabled"]:
        raise HomeAssistantError("Home Assistant is not enabled.")
    if not ha["base_url"]:
        raise HomeAssistantError("Home Assistant URL must be local.")
    if not ha["token"]:
        raise HomeAssistantError("Home Assistant token is missing.")

    data = None
    headers = {
        "Authorization": f"Bearer {ha['token']}",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(
        ha["base_url"] + "/" + path.lstrip("/"),
        data=data,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except Exception as exc:
        raise HomeAssistantError(f"Home Assistant request failed: {exc}") from exc
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None
