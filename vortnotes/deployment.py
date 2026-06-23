"""Deployment configuration helpers shared by local and production entrypoints."""

from __future__ import annotations

import json
import os
from pathlib import Path


def truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def direct_https_config(config_path: Path | None = None) -> dict[str, object]:
    """Return effective direct-HTTPS settings, with environment taking priority."""
    cert = os.getenv("VORTNOTES_TLS_CERT_FILE", "").strip()
    key = os.getenv("VORTNOTES_TLS_KEY_FILE", "").strip()
    env_override = bool(cert or key)
    enabled = env_override
    if not env_override and config_path is not None and config_path.is_file():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            https = raw.get("https") if isinstance(raw, dict) else {}
            https = https if isinstance(https, dict) else {}
            enabled = bool(https.get("enabled"))
            cert = str(https.get("cert_file") or "").strip()
            key = str(https.get("key_file") or "").strip()
        except Exception:
            enabled = False
            cert = ""
            key = ""
    return {
        "enabled": enabled,
        "cert_file": cert,
        "key_file": key,
        "env_override": env_override,
    }


def tls_context_from_config(config_path: Path | None = None) -> tuple[str, str] | None:
    """Return a validated SSL context from environment or persisted config."""
    config = direct_https_config(config_path)
    if not config["enabled"]:
        return None
    cert = str(config["cert_file"])
    key = str(config["key_file"])
    if bool(cert) != bool(key):
        raise RuntimeError("VORTNOTES_TLS_CERT_FILE and VORTNOTES_TLS_KEY_FILE must be set together")
    if not cert or not key:
        raise RuntimeError("Direct HTTPS is enabled but certificate and private-key paths are not configured")
    for label, value in (("certificate", cert), ("private key", key)):
        if not Path(value).is_file():
            raise RuntimeError(f"TLS {label} file does not exist: {value}")
        if not os.access(value, os.R_OK):
            raise RuntimeError(f"TLS {label} file is not readable by the VortNotes user: {value}")
    return cert, key


def tls_context_from_env() -> tuple[str, str] | None:
    """Backward-compatible environment-only helper."""
    return tls_context_from_config()
