import io
import json
import sqlite3
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
    assert client.get("/content/apps/snake").status_code == 200
    assert client.get("/content/apps/calendar").status_code == 200
    assert client.get("/content/apps/kanban").status_code == 200
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


def test_new_content_apps_are_available_in_add_dropdowns():
    app = create_app()
    app.config["TESTING"] = True
    name = f"content_apps_{uuid.uuid4().hex}.db"
    ensure_db_initialized(resolve_db_path(name))

    client = app.test_client()
    client.set_cookie("selected_db", name)
    page = client.get("/content")

    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert '<option value="kanban">Kanban Board</option>' in html
    assert '<option value="calendar">Calendar Lite</option>' in html
    assert '<option value="snake">Snake</option>' in html
    assert "Built-in apps run locally inside Vortnotes." in html


def test_kanban_app_initializes_and_links_cards_to_notes():
    app = create_app()
    app.config["TESTING"] = True
    name = f"kanban_{uuid.uuid4().hex}.db"
    db_path = resolve_db_path(name)
    ensure_db_initialized(db_path)

    with sqlite3.connect(db_path) as db:
        cur = db.execute(
            "INSERT INTO notes (title, tag, created_at, content_html, content_delta) VALUES (?,?,?,?,?)",
            ("Project note", "work", "2026-01-01T00:00:00Z", "<p>Details</p>", ""),
        )
        note_id = int(cur.lastrowid)
        db.commit()

    client = app.test_client()
    client.set_cookie("selected_db", name)
    with client.session_transaction() as session:
        session["_csrf_token"] = "kanban-token"
    page = client.get("/content/apps/kanban")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert "Kanban Board" in html
    assert "Backlog" in html
    assert "Doing" in html
    assert "Done" in html
    assert "Project note" in html
    assert "kanban_app.js" in html

    with sqlite3.connect(db_path) as db:
        column_id = db.execute("SELECT id FROM kanban_columns WHERE title='Backlog'").fetchone()[0]

    card = client.post(
        "/content/apps/kanban/card/save",
        json={"column_id": column_id, "title": "Wire up board", "body": "Use this note.", "note_id": note_id},
        headers={"X-CSRFToken": "kanban-token", "X-Requested-With": "XMLHttpRequest"},
    )
    assert card.status_code == 200
    card_json = card.get_json()
    assert card_json["ok"] is True
    assert card_json["card"]["note_id"] == note_id

    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "SELECT title, body, note_id FROM kanban_cards WHERE id=?", (card_json["card"]["id"],)
        ).fetchone()
    assert row == ("Wire up board", "Use this note.", note_id)


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


def test_locked_camera_hls_does_not_return_settings_html():
    app = create_app()
    app.config["TESTING"] = True
    name = f"locked_camera_{uuid.uuid4().hex}.db"
    path = resolve_db_path(name)
    ensure_db_initialized(path)
    set_db_password(path, "secret")
    set_db_guest_permissions(
        name,
        {
            "notes": "none",
            "content": "none",
            "apps": False,
            "home_assistant": False,
        },
    )

    client = app.test_client()
    client.set_cookie("selected_db", name)

    response = client.get("/content/camera/1/hls/stream.m3u8", follow_redirects=True)

    assert response.status_code == 401
    assert response.content_type.startswith("text/plain")
    assert b"Camera stream access requires unlocking this database." in response.data
    assert b"<html" not in response.data.lower()
    assert b"db-accordion" not in response.data


def test_read_without_password_allows_camera_stream_lifecycle():
    app = create_app()
    app.config["TESTING"] = True
    name = f"readonly_camera_{uuid.uuid4().hex}.db"
    path = resolve_db_path(name)
    ensure_db_initialized(path)
    set_db_password(path, "secret")
    set_db_read_without_password(name, True)

    client = app.test_client()
    client.set_cookie("selected_db", name)
    with client.session_transaction() as session:
        session["_csrf_token"] = "readonly-camera-token"

    hls = client.get("/content/camera/1/hls/stream.m3u8")
    assert hls.status_code == 503
    assert b"auth_required" not in hls.data
    assert b"db-accordion" not in hls.data

    start = client.post(
        "/content/camera/1/stream/start",
        headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": "readonly-camera-token",
        },
    )
    assert start.status_code == 400
    assert start.get_json()["error"] == "rtsp_playback_not_configured"

    stop = client.post(
        "/content/camera/1/stream/stop",
        headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": "readonly-camera-token",
        },
    )
    assert stop.status_code == 200
    assert stop.get_json()["ok"] is True


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


def test_content_camera_uses_saved_profile_as_single_source_of_truth():
    from vortnotes.webapp import load_config, save_config

    app = create_app()
    app.config["TESTING"] = True
    name = f"camera_profile_{uuid.uuid4().hex}.db"
    path = resolve_db_path(name)
    ensure_db_initialized(path)
    original_cfg = load_config()
    profile_id = f"front-door-{uuid.uuid4().hex}"

    try:
        cfg = dict(original_cfg)
        cfg["cameras"] = [
            {
                "id": profile_id,
                "title": "Front Door",
                "vendor": "reolink",
                "playback_mode": "snapshot",
                "base_url": "http://192.168.1.10",
                "username": "viewer",
                "password": "secret",
                "channel": 0,
                "refresh_ms": 500,
                "ptz_mode": "reolink",
                "stream": "",
            }
        ]
        save_config(cfg)

        client = app.test_client()
        client.set_cookie("selected_db", name)
        with client.session_transaction() as session:
            session["_csrf_token"] = "camera-profile-token"

        created = client.post(
            "/content/item/save",
            data={
                "csrf_token": "camera-profile-token",
                "mode": "add",
                "row_type": "camera",
                "title": "Front Door Tile",
                "camera_profile_id": profile_id,
                "group_id": "",
                "return_to": "/content/edit",
            },
        )
        assert created.status_code == 302

        with sqlite3.connect(path) as db:
            row = db.execute("SELECT url FROM links WHERE item_kind='camera' AND title='Front Door Tile'").fetchone()
        assert row is not None
        assert json.loads(row[0]) == {"profile_id": profile_id}

        # Older profile-linked rows could retain a per-item PTZ override. The
        # saved profile must remain authoritative for those rows as well.
        with sqlite3.connect(path) as db:
            db.execute(
                "UPDATE links SET url=? WHERE item_kind='camera' AND title='Front Door Tile'",
                (json.dumps({"profile_id": profile_id, "ptz_mode": "none"}),),
            )
            db.commit()

        first_page = client.get("/content/edit")
        first_html = first_page.data.decode("utf-8")
        assert 'data-camera-base-url="http://192.168.1.10"' in first_html
        assert 'data-camera-ptz-mode="reolink"' in first_html
        assert 'name="camera_base_url"' not in first_html
        assert 'name="camera_password"' not in first_html
        assert ">Custom</option>" not in first_html

        cfg["cameras"][0]["base_url"] = "http://192.168.1.11"
        cfg["cameras"][0]["refresh_ms"] = 1250
        save_config(cfg)

        updated_page = client.get("/content/edit")
        updated_html = updated_page.data.decode("utf-8")
        assert 'data-camera-base-url="http://192.168.1.11"' in updated_html
        assert 'data-camera-refresh-ms="1250"' in updated_html
        assert 'data-camera-base-url="http://192.168.1.10"' not in updated_html
    finally:
        save_config(original_cfg)


def test_reolink_live_profile_derives_rtsp_from_single_credentials():
    from vortnotes.webapp import load_config, save_config, set_admin_password

    original_cfg = load_config()
    try:
        app = create_app()
        app.config["TESTING"] = True
        set_admin_password("test-password")
        client = app.test_client()
        with client.session_transaction() as session:
            session["admin_authed"] = True
            session["_csrf_token"] = "reolink-profile-token"

        response = client.post(
            "/settings/cameras",
            data={
                "csrf_token": "reolink-profile-token",
                "camera_id": "",
                "camera_title": "Driveway",
                "camera_vendor": "reolink",
                "camera_playback_mode": "rtsp",
                "camera_base_url": "http://192.168.1.50",
                "camera_username": "camera user",
                "camera_password": "p@ss word",
                "camera_channel": "1",
                "camera_refresh_ms": "500",
                "camera_ptz_mode": "reolink",
                "camera_stream_quality": "sub",
                "camera_stream": "",
            },
        )
        assert response.status_code == 302

        saved = load_config()["cameras"][0]
        assert saved["stream"] == ""
        assert saved["username"] == "camera user"
        assert saved["password"] == "p@ss word"
        assert saved["stream_quality"] == "sub"

        name = f"reolink_profile_{uuid.uuid4().hex}.db"
        path = resolve_db_path(name)
        ensure_db_initialized(path)
        with sqlite3.connect(path) as db:
            db.execute(
                "INSERT INTO links (title, url, target, created_at, display_order, item_kind, embed) "
                "VALUES (?, ?, '_self', '', 0, 'camera', 0)",
                ("Driveway Tile", json.dumps({"profile_id": saved["id"]})),
            )
            db.commit()

        client.set_cookie("selected_db", name)
        page = client.get("/content/edit")
        html = page.data.decode("utf-8")
        assert 'data-camera-stream="rtsp://camera%20user:p%40ss%20word@192.168.1.50:554/' 'h264Preview_02_sub"' in html
    finally:
        save_config(original_cfg)


def test_empty_content_page_keeps_right_click_add_target():
    app = create_app()
    app.config["TESTING"] = True
    name = f"empty_content_{uuid.uuid4().hex}.db"
    ensure_db_initialized(resolve_db_path(name))

    client = app.test_client()
    client.set_cookie("selected_db", name)
    page = client.get("/content")

    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert 'id="vnContentGrid"' in html
    assert "No content yet. Right-click here to add your first item." in html
    assert "grid.addEventListener('contextmenu'" in html
    assert 'onclick="vnContentContextAdd()"' in html


def test_content_media_modal_has_loop_and_continue_playlist_controls():
    app = create_app()
    app.config["TESTING"] = True
    name = f"content_media_playlist_{uuid.uuid4().hex}.db"
    db_path = resolve_db_path(name)
    ensure_db_initialized(db_path)

    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO links (title, url, target, created_at, display_order, item_kind, "
            "file_stored_name, file_original_name, file_mime, file_size) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "Song one",
                "media/song-one.mp3",
                "_blank",
                "2026-01-01T00:00:00Z",
                0,
                "file",
                "media/song-one.mp3",
                "song-one.mp3",
                "audio/mpeg",
                123,
            ),
        )
        db.execute(
            "INSERT INTO links (title, url, target, created_at, display_order, item_kind, "
            "file_stored_name, file_original_name, file_mime, file_size) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "Video one",
                "media/video-one.mp4",
                "_blank",
                "2026-01-01T00:00:01Z",
                1,
                "file",
                "media/video-one.mp4",
                "video-one.mp4",
                "video/mp4",
                456,
            ),
        )
        db.commit()

    client = app.test_client()
    client.set_cookie("selected_db", name)
    page = client.get("/content")

    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert 'id="contentViewLoop"' in html
    assert 'id="contentViewContinue"' in html
    assert 'id="contentViewShuffle"' in html
    assert "Continue in folder" in html
    assert "Shuffle" in html
    assert 'data-media-mime="audio/mpeg"' in html
    assert 'data-media-title="song-one.mp3"' in html
    assert 'data-media-playable="1"' in html
    assert "vnBuildMediaPlaylist" in html
    assert "vnOpenPlaylistOffset" in html
    assert "vnMediaShuffle" in html
    assert "vnSetMediaMode" in html
    assert "vnMediaShuffleQueue" in html
    assert "shuffle.disabled = vnMediaPlaybackPrefs.loop" in html


def test_note_attachment_media_modal_has_loop_and_continue_playlist_controls():
    app = create_app()
    app.config["TESTING"] = True
    name = f"note_media_playlist_{uuid.uuid4().hex}.db"
    db_path = resolve_db_path(name)
    ensure_db_initialized(db_path)

    with sqlite3.connect(db_path) as db:
        cur = db.execute(
            "INSERT INTO notes (title, tag, created_at, content_html, content_delta) VALUES (?,?,?,?,?)",
            ("Media note", "", "2026-01-01T00:00:00Z", "<p>Media</p>", ""),
        )
        note_id = cur.lastrowid
        db.execute(
            "INSERT INTO attachments (note_id, original_name, stored_name, created_at, display_order) "
            "VALUES (?,?,?,?,?)",
            (note_id, "first.mp3", "first-stored.bin", "2026-01-01T00:00:01Z", 0),
        )
        db.execute(
            "INSERT INTO attachments (note_id, original_name, stored_name, created_at, display_order) "
            "VALUES (?,?,?,?,?)",
            (note_id, "second.mp4", "second-stored.bin", "2026-01-01T00:00:02Z", 1),
        )
        db.commit()

    client = app.test_client()
    client.set_cookie("selected_db", name)
    page = client.get(f"/notes/{note_id}")

    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert 'id="contentViewLoop"' in html
    assert 'id="contentViewContinue"' in html
    assert 'id="contentViewShuffle"' in html
    assert "Continue in folder" in html
    assert "Shuffle" in html
    assert 'data-mime="audio/mpeg"' in html
    assert 'data-media-title="first.mp3"' in html
    assert 'data-media-playable="1"' in html
    assert "vnBuildMediaPlaylist" in html
    assert "vnOpenPlaylistOffset" in html
    assert "vnMediaShuffle" in html
    assert "vnSetMediaMode" in html
    assert "vnMediaShuffleQueue" in html
    assert "shuffle.disabled = vnMediaPlaybackPrefs.loop" in html


def test_empty_content_group_modal_keeps_right_click_add_target():
    app = create_app()
    app.config["TESTING"] = True
    name = f"empty_group_content_{uuid.uuid4().hex}.db"
    db_path = resolve_db_path(name)
    ensure_db_initialized(db_path)

    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO link_groups (name, icon_stored_name, created_at, display_order) VALUES (?,?,?,?)",
            ("Empty Group", None, "2026-01-01T00:00:00Z", 0),
        )
        db.commit()

    client = app.test_client()
    client.set_cookie("selected_db", name)
    page = client.get("/content")

    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert "No items yet. Right-click here to add one to this group." in html
    assert "grid.style.minHeight = '180px'" in html
    assert "window.vnMakeContentReorder(grid" in html


def test_content_file_upload_to_group_xhr_redirects_back_to_content():
    app = create_app()
    app.config["TESTING"] = True
    name = f"content_group_upload_{uuid.uuid4().hex}.db"
    db_path = resolve_db_path(name)
    ensure_db_initialized(db_path)

    with sqlite3.connect(db_path) as db:
        cur = db.execute(
            "INSERT INTO link_groups (name, icon_stored_name, created_at, display_order) VALUES (?,?,?,?)",
            ("Media", None, "2026-01-01T00:00:00Z", 0),
        )
        group_id = cur.lastrowid
        db.commit()

    client = app.test_client()
    client.set_cookie("selected_db", name)
    with client.session_transaction() as session:
        session["_csrf_token"] = "content-upload-token"

    response = client.post(
        "/content/item/save",
        data={
            "csrf_token": "content-upload-token",
            "mode": "add",
            "row_type": "file",
            "title": "Grouped audio",
            "group_id": str(group_id),
            "file_file": (io.BytesIO(b"fake audio"), "song.mp3"),
        },
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 200
    assert response.get_json()["redirect"] == "/content"
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "SELECT title, group_id, item_kind, file_original_name, file_mime FROM links WHERE group_id=?",
            (group_id,),
        ).fetchone()
    assert row == ("Grouped audio", group_id, "file", "song.mp3", "audio/mpeg")
