import json
from pathlib import Path

import pytest

from vortnotes.deployment import direct_https_config, tls_context_from_config, tls_context_from_env
from vortnotes.settings import CONFIG_PATH


def test_tls_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VORTNOTES_TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("VORTNOTES_TLS_KEY_FILE", raising=False)
    assert tls_context_from_env() is None


def test_tls_requires_both_files(monkeypatch, tmp_path: Path):
    cert = tmp_path / "cert.pem"
    cert.write_text("certificate", encoding="utf-8")
    monkeypatch.setenv("VORTNOTES_TLS_CERT_FILE", str(cert))
    monkeypatch.delenv("VORTNOTES_TLS_KEY_FILE", raising=False)
    with pytest.raises(RuntimeError, match="must be set together"):
        tls_context_from_env()


def test_tls_context_uses_existing_files(monkeypatch, tmp_path: Path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert.write_text("certificate", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    monkeypatch.setenv("VORTNOTES_TLS_CERT_FILE", str(cert))
    monkeypatch.setenv("VORTNOTES_TLS_KEY_FILE", str(key))
    assert tls_context_from_env() == (str(cert), str(key))


def test_persisted_https_config(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("VORTNOTES_TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("VORTNOTES_TLS_KEY_FILE", raising=False)
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert.write_text("certificate", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(
        '{"https":{"enabled":true,"cert_file":"%s","key_file":"%s"}}'
        % (str(cert).replace("\\", "\\\\"), str(key).replace("\\", "\\\\")),
        encoding="utf-8",
    )
    assert tls_context_from_config(config) == (str(cert), str(key))
    assert direct_https_config(config)["env_override"] is False


def test_environment_https_overrides_persisted_config(monkeypatch, tmp_path: Path):
    env_cert, env_key = tmp_path / "env-cert.pem", tmp_path / "env-key.pem"
    env_cert.write_text("certificate", encoding="utf-8")
    env_key.write_text("key", encoding="utf-8")
    monkeypatch.setenv("VORTNOTES_TLS_CERT_FILE", str(env_cert))
    monkeypatch.setenv("VORTNOTES_TLS_KEY_FILE", str(env_key))
    config = tmp_path / "config.json"
    config.write_text('{"https":{"enabled":false}}', encoding="utf-8")
    assert tls_context_from_config(config) == (str(env_cert), str(env_key))
    assert direct_https_config(config)["env_override"] is True


def test_admin_can_save_direct_https_configuration(tmp_path: Path):
    from vortnotes import create_app
    from vortnotes.webapp import list_db_files, set_admin_password

    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert.write_text("certificate", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    app = create_app()
    app.config["TESTING"] = True
    set_admin_password("test-password")
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_authed"] = True
        session["_csrf_token"] = "test-token"
    response = client.post(
        "/settings/https",
        data={
            "csrf_token": "test-token",
            "enabled": "1",
            "cert_file": str(cert),
            "key_file": str(key),
        },
    )
    assert response.status_code == 302
    saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["https"] == {"enabled": True, "cert_file": str(cert), "key_file": str(key)}

    page = client.get("/settings")
    assert b'id="https-config"' in page.data
    assert b"Enable direct HTTPS" in page.data
    assert page.data.index(b">Config<") < page.data.index(b'id="https-config"') < page.data.index(b">Admin Access<")
    assert page.data.count(b'class="perm-read-toggle"') == len(list_db_files())
