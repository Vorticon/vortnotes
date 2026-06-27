import io
import uuid

from vortnotes import create_app
from vortnotes.webapp import (
    ensure_db_initialized,
    resolve_db_path,
    set_db_guest_permissions,
    set_db_password,
    set_db_read_without_password,
)


def test_413_json_message_on_api_upload():
    app = create_app()
    app.config["TESTING"] = True
    # Force a very small request limit to trigger 413.
    app.config["MAX_CONTENT_LENGTH"] = 1024  # 1KB

    client = app.test_client()
    data = {
        "file": (io.BytesIO(b"a" * 2048), "big.png"),
    }
    r = client.post("/api/inline-upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 413
    j = r.get_json()
    assert j["error"] == "The upload exceeded the maximum request size."
    assert j["max_mb"] == 0  # 1KB rounds down to 0MB


def test_builtin_content_apps_render_and_reject_unknown_apps():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    assert client.get("/content/apps/tetris").status_code == 200
    assert client.get("/content/apps/jewels").status_code == 200
    assert client.get("/content/apps/memory").status_code == 200
    assert client.get("/content/apps/minesweeper").status_code == 200
    assert client.get("/content/apps/breakout").status_code == 200
    assert client.get("/content/apps/2048").status_code == 404
    assert client.get("/content/apps/simon").status_code == 200
    assert client.get("/content/apps/sticky").status_code == 200
    assert client.get("/content/apps/ambient").status_code == 200
    assert client.get("/content/apps/not-an-app").status_code == 404


def test_settings_database_actions_use_modals_not_native_prompts():
    from vortnotes.webapp import set_admin_password

    app = create_app()
    app.config["TESTING"] = True
    set_admin_password("test-password")
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_authed"] = True

    page = client.get("/settings")
    assert page.status_code == 200
    html = page.data.decode("utf-8")

    for modal_id in (
        "dbCreateModal",
        "dbImportModal",
        "dbAutoBackupModal",
        "dbRenameModal",
        "dbPasswordModal",
        "dbBackupModal",
        "dbDeleteModal",
        "dbAppearanceModal",
        "dbPermissionsModal",
    ):
        assert f'id="{modal_id}"' in html

    assert 'id="btnDbReset"' not in html
    assert "Read without password" not in html
    assert "Guest Permissions" in html
    assert "prompt(" not in html
    assert "Rename DB:" not in html
    assert "Reset (clear) DB password" not in html
    assert "Delete DB '" not in html
    assert "Clear Password" in html


def test_database_selection_shows_admin_access_notice():
    from vortnotes.webapp import set_admin_password

    app = create_app()
    app.config["TESTING"] = True
    set_admin_password("test-password")
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_authed"] = True

    page = client.get("/settings")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert "Admin is logged in, so all databases are accessible" in html
    assert 'data-open-modal="configAdminModal"' in html


def test_settings_uses_admin_login_modal_instead_of_admin_login_link():
    from vortnotes.webapp import set_admin_password

    app = create_app()
    app.config["TESTING"] = True
    set_admin_password("test-password")
    client = app.test_client()

    page = client.get("/settings")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert 'id="settingsAdminLoginModal"' in html
    assert 'data-open-modal="settingsAdminLoginModal"' in html
    assert 'href="/db/admin-login' not in html


def test_protected_db_action_redirects_to_settings_admin_modal():
    from vortnotes.webapp import set_admin_password

    app = create_app()
    app.config["TESTING"] = True
    set_admin_password("test-password")
    client = app.test_client()
    with client.session_transaction() as session:
        session["_csrf_token"] = "admin-modal-token"

    response = client.post("/db/backup", data={"csrf_token": "admin-modal-token", "name": "notes.db"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings?admin_login=1")


def test_settings_config_actions_use_modals():
    from vortnotes.settings import DATA_DIR
    from vortnotes.webapp import set_admin_password

    app = create_app()
    app.config["TESTING"] = True
    set_admin_password("test-password")
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "modal-test-backup.zip").write_bytes(b"PK\x05\x06" + (b"\0" * 18))
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_authed"] = True

    page = client.get("/settings")
    assert page.status_code == 200
    html = page.data.decode("utf-8")

    for modal_id in (
        "configUploadModal",
        "configStorageModal",
        "configSystemModal",
        "configHttpsModal",
        "configHomeAssistantModal",
        "configAdminModal",
    ):
        assert f'id="{modal_id}"' in html

    assert "Upload Limits" in html
    assert "CPU usage" in html
    assert "Memory usage" in html
    assert "Save Upload Config" in html
    assert "Save HTTPS Configuration" in html
    assert "Save Home Assistant" in html
    assert "Saved Backups" in html
    assert "/db/backup/download/modal-test-backup.zip" in html
    assert "/db/backup/delete" in html
    assert 'class="db-backup-link"' in html
    assert ">Delete</button>" in html


def test_manual_backup_saves_zip_without_immediate_download():
    from vortnotes.settings import DATA_DIR
    from vortnotes.webapp import set_admin_password

    app = create_app()
    app.config["TESTING"] = True
    name = f"manual_backup_{uuid.uuid4().hex}.db"
    ensure_db_initialized(resolve_db_path(name))
    set_admin_password("test-password")
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_authed"] = True
        session["_csrf_token"] = "backup-token"

    response = client.post("/db/backup", data={"csrf_token": "backup-token", "name": name})

    assert response.status_code == 302
    assert "Backup+created" not in response.headers["Location"]
    assert response.headers["Location"].endswith("/settings")
    assert "attachment" not in response.headers.get("Content-Disposition", "").lower()
    backups = list((DATA_DIR / "backups").glob(f"{name.removesuffix('.db')}_manual_*.zip"))
    assert backups


def test_auto_backup_selection_is_saved():
    from vortnotes.webapp import load_config, set_admin_password

    app = create_app()
    app.config["TESTING"] = True
    first = f"auto_a_{uuid.uuid4().hex}.db"
    second = f"auto_b_{uuid.uuid4().hex}.db"
    ensure_db_initialized(resolve_db_path(first))
    ensure_db_initialized(resolve_db_path(second))
    set_admin_password("test-password")
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_authed"] = True
        session["_csrf_token"] = "auto-token"

    response = client.post(
        "/db/auto-backup-set",
        data={
            "csrf_token": "auto-token",
            "enabled": "1",
            "interval_hours": "12",
            "db_names": [first],
        },
    )

    assert response.status_code == 302
    auto_backup = load_config()["auto_backup"]
    assert auto_backup["enabled"] is True
    assert auto_backup["interval_hours"] == 12
    assert auto_backup["dbs"] == [first]


def test_settings_ignores_url_status_parameters():
    from vortnotes.webapp import set_admin_password

    app = create_app()
    app.config["TESTING"] = True
    set_admin_password("test-password")
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_authed"] = True

    response = client.get("/settings?notice=<script>alert(1)</script>&error=edited-url-message")

    assert response.status_code == 200
    assert b"alert(1)" not in response.data
    assert b"edited-url-message" not in response.data

    db_redirect = client.get("/db?error=edited-db-url-message")
    assert db_redirect.status_code == 302
    assert "error=" not in db_redirect.headers["Location"]


def test_read_without_password_allows_apps_and_ha_actions_but_not_db_writes():
    app = create_app()
    app.config["TESTING"] = True
    name = f"readonly_{uuid.uuid4().hex}.db"
    path = resolve_db_path(name)
    ensure_db_initialized(path)
    set_db_password(path, "secret")
    set_db_read_without_password(name, True)

    client = app.test_client()
    client.set_cookie("selected_db", name)
    with client.session_transaction() as session:
        session["_csrf_token"] = "readonly-token"

    assert client.get("/content/apps/tetris").status_code == 200
    ha = client.post(
        "/content/ha/activate",
        json={},
        headers={"X-CSRFToken": "readonly-token", "X-Requested-With": "XMLHttpRequest"},
    )
    assert ha.status_code == 400
    assert ha.get_json()["error"] == "missing_item"

    sticky = client.post(
        "/content/apps/sticky/save",
        json={"title": "blocked"},
        headers={"X-CSRFToken": "readonly-token", "X-Requested-With": "XMLHttpRequest"},
    )
    assert sticky.status_code in {301, 302, 401}


def test_guest_permissions_allow_notes_write_without_content_access():
    app = create_app()
    app.config["TESTING"] = True
    name = f"guest_notes_{uuid.uuid4().hex}.db"
    path = resolve_db_path(name)
    ensure_db_initialized(path)
    set_db_password(path, "secret")
    set_db_guest_permissions(
        name,
        {
            "notes": "write",
            "content": "none",
            "apps": False,
            "home_assistant": False,
        },
    )

    client = app.test_client()
    client.set_cookie("selected_db", name)
    with client.session_transaction() as session:
        session["_csrf_token"] = "guest-notes-token"

    content_page = client.get("/content")
    assert content_page.status_code == 302
    assert "/settings" in content_page.headers["Location"]

    new_page = client.get("/notes/new")
    assert new_page.status_code == 200

    created = client.post(
        "/notes/new",
        data={
            "csrf_token": "guest-notes-token",
            "title": "Guest writable note",
            "tag": "",
            "content_html": "<p>Hello</p>",
        },
    )
    assert created.status_code == 302
    assert "/notes/" in created.headers["Location"]


def test_remembered_db_login_allows_switching_until_logout():
    app = create_app()
    app.config["TESTING"] = True
    first = f"remember_a_{uuid.uuid4().hex}.db"
    second = f"remember_b_{uuid.uuid4().hex}.db"
    for name, password in ((first, "alpha"), (second, "beta")):
        path = resolve_db_path(name)
        ensure_db_initialized(path)
        set_db_password(path, password)

    client = app.test_client()
    with client.session_transaction() as session:
        session["_csrf_token"] = "remember-token"

    unlocked = client.post(
        "/db/select",
        data={
            "csrf_token": "remember-token",
            "name": first,
            "password": "alpha",
            "remember": "1",
            "next": "/content",
        },
    )
    assert unlocked.status_code == 302
    with client.session_transaction() as session:
        assert session["remember_all_dbs"] is True

    switched = client.post(
        "/db/select",
        data={"csrf_token": "remember-token", "name": second, "password": "", "next": "/content"},
    )
    assert switched.status_code == 302
    assert switched.headers["Location"].endswith("/content")

    page = client.get("/settings")
    assert b"This database already has an active login session." in page.data
    assert f"Log out of {second}".encode() in page.data

    # Simulate changing the dropdown without first opening that DB. Logout must
    # target the submitted dropdown value, not the older selection cookie.
    client.set_cookie("selected_db", first)
    logged_out = client.post("/logout", data={"csrf_token": "remember-token", "name": second})
    assert logged_out.status_code == 302
    with client.session_transaction() as session:
        assert session["remember_all_dbs"] is True
        assert session["db_logout_overrides"][second] is True

    selected_page = client.get("/settings")
    assert f"Log out of {second}".encode() in selected_page.data
    assert b'id="dbLogoutForm"' in selected_page.data
    assert b"display:none;" in selected_page.data
    assert b"This database is password protected." in selected_page.data

    other_db_still_open = client.post(
        "/db/select",
        data={"csrf_token": "remember-token", "name": first, "password": "", "next": "/content"},
    )
    assert other_db_still_open.status_code == 302
    assert other_db_still_open.headers["Location"].endswith("/content")

    locked_again = client.post(
        "/db/select",
        data={"csrf_token": "remember-token", "name": second, "password": "", "next": "/content"},
    )
    assert locked_again.status_code == 302
    assert "db_error=Password+required" in locked_again.headers["Location"]
