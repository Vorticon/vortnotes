"""Deployment configuration helpers shared by local and production entrypoints."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

SELF_SIGNED_TLS_DIRNAME = "tls"
SELF_SIGNED_TLS_CERT_NAME = "vortnotes-selfsigned.crt"
SELF_SIGNED_TLS_KEY_NAME = "vortnotes-selfsigned.key"


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


def self_signed_tls_paths(data_dir: Path) -> tuple[Path, Path]:
    """Return the default per-install self-signed TLS certificate paths."""
    tls_dir = data_dir / "config" / SELF_SIGNED_TLS_DIRNAME
    return tls_dir / SELF_SIGNED_TLS_CERT_NAME, tls_dir / SELF_SIGNED_TLS_KEY_NAME


def generate_self_signed_tls_cert(
    data_dir: Path,
    *,
    common_name: str = "VortNotes Local",
    days_valid: int = 825,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    """Generate a unique self-signed certificate/key pair for this install.

    The private key is written under NOTES_DATA_DIR, not baked into the image.
    Browsers will still warn because the certificate is self-signed, but each
    installation gets its own keypair.
    """
    cert_path, key_path = self_signed_tls_paths(data_dir)
    if not overwrite and cert_path.exists() and key_path.exists():
        return cert_path, key_path

    from datetime import datetime, timedelta, timezone
    from ipaddress import ip_address

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    tls_dir = cert_path.parent
    tls_dir.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    hostname = socket.gethostname() or "localhost"
    names = {"localhost", hostname, f"{hostname}.local"}
    dns_names = sorted(n for n in names if n)
    ip_addresses = ["127.0.0.1", "::1"]

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "VortNotes"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    san_entries = [x509.DNSName(name) for name in dns_names]
    san_entries.extend(x509.IPAddress(ip_address(value)) for value in ip_addresses)

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=max(1, int(days_valid))))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    try:
        os.chmod(key_path, 0o600)
        os.chmod(cert_path, 0o644)
    except OSError:
        pass
    return cert_path, key_path


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
