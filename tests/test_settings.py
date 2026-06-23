from vortnotes import create_app, settings


def test_settings_defaults_are_sane():
    assert settings.INLINE_IMAGE_MAX_MB >= 1
    assert settings.ATTACHMENT_MAX_GB >= 1


def test_app_has_max_content_length():
    app = create_app()
    assert app.config.get("MAX_CONTENT_LENGTH") is not None


def test_settings_defaults_match_expected_when_env_unset(monkeypatch):
    # Ensure env vars aren't overriding defaults for this test.
    monkeypatch.delenv("VORTNOTES_INLINE_IMAGE_MAX_MB", raising=False)
    monkeypatch.delenv("VORTNOTES_ATTACHMENT_MAX_GB", raising=False)
    monkeypatch.delenv("VORTNOTES_MAX_CONTENT_LENGTH_MB", raising=False)

    import importlib

    from vortnotes import settings as settings_mod

    importlib.reload(settings_mod)

    assert settings_mod.INLINE_IMAGE_MAX_MB == 50
    assert settings_mod.ATTACHMENT_MAX_GB == 5
    assert settings_mod.MAX_CONTENT_LENGTH_MB == 50


def test_legacy_app_mount_preferred_for_docker_data_dir(tmp_path, monkeypatch):
    legacy_app = tmp_path / "app"
    (legacy_app / "dbs").mkdir(parents=True)
    (legacy_app / "dbs" / "Kids.db").write_bytes(b"legacy")

    from vortnotes import settings as settings_mod

    monkeypatch.setenv("NOTES_DATA_DIR", "/data")
    monkeypatch.setattr(settings_mod, "BASE_DIR", legacy_app)

    assert settings_mod._resolve_data_dir() == legacy_app


def test_requested_data_dir_used_without_legacy_mounts(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    legacy_app = tmp_path / "app"
    legacy_app.mkdir()

    from vortnotes import settings as settings_mod

    monkeypatch.setenv("NOTES_DATA_DIR", str(data_dir))
    monkeypatch.setattr(settings_mod, "BASE_DIR", legacy_app)

    assert settings_mod._resolve_data_dir() == data_dir.resolve()


def test_legacy_app_mount_can_migrate_to_requested_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    legacy_app = tmp_path / "app"
    (legacy_app / "dbs").mkdir(parents=True)
    (legacy_app / "uploads").mkdir()
    (legacy_app / "config").mkdir()
    (data_dir / "dbs").mkdir(parents=True)

    (legacy_app / "dbs" / "notes.db").write_bytes(b"legacy-notes")
    (legacy_app / "dbs" / "Kids.db").write_bytes(b"kids")
    (legacy_app / "uploads" / "file.txt").write_text("upload", encoding="utf-8")
    (legacy_app / "config" / "config.json").write_text('{"default_db":"Kids.db"}', encoding="utf-8")
    (legacy_app / ".secret_key").write_text("legacy-secret", encoding="utf-8")
    (data_dir / "dbs" / "notes.db").write_bytes(b"fresh-notes")

    from vortnotes import settings as settings_mod

    monkeypatch.setenv("NOTES_DATA_DIR", "/data")
    monkeypatch.setenv("VORTNOTES_MIGRATE_LEGACY_APP_DATA", "1")
    monkeypatch.setenv("VORTNOTES_MIGRATION_STAMP", "20260101_000000")
    monkeypatch.setattr(settings_mod, "BASE_DIR", legacy_app)
    monkeypatch.setattr(settings_mod, "_get_env_path", lambda _name, _default: data_dir)

    assert settings_mod._resolve_data_dir() == data_dir
    assert (data_dir / "dbs" / "notes.db").read_bytes() == b"legacy-notes"
    assert (data_dir / "dbs" / "notes.pre_migration_20260101_000000.db").read_bytes() == b"fresh-notes"
    assert (data_dir / "dbs" / "Kids.db").read_bytes() == b"kids"
    assert (data_dir / "uploads" / "file.txt").read_text(encoding="utf-8") == "upload"
    assert (data_dir / "config" / "config.json").read_text(encoding="utf-8") == '{"default_db":"Kids.db"}'
    assert (data_dir / ".secret_key").read_text(encoding="utf-8") == "legacy-secret"
    assert (data_dir / ".legacy_app_migration_complete").exists()


def test_legacy_migration_marker_prevents_repeat_copy(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    legacy_app = tmp_path / "app"
    (legacy_app / "dbs").mkdir(parents=True)
    (data_dir / "dbs").mkdir(parents=True)
    (legacy_app / "dbs" / "notes.db").write_bytes(b"legacy")
    (data_dir / "dbs" / "notes.db").write_bytes(b"already-migrated")
    (data_dir / ".legacy_app_migration_complete").write_text("done", encoding="utf-8")

    from vortnotes import settings as settings_mod

    monkeypatch.setenv("NOTES_DATA_DIR", "/data")
    monkeypatch.setenv("VORTNOTES_MIGRATE_LEGACY_APP_DATA", "1")
    monkeypatch.setattr(settings_mod, "BASE_DIR", legacy_app)
    monkeypatch.setattr(settings_mod, "_get_env_path", lambda _name, _default: data_dir)

    assert settings_mod._resolve_data_dir() == data_dir
    assert (data_dir / "dbs" / "notes.db").read_bytes() == b"already-migrated"
