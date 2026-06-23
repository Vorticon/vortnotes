import io
import uuid

from vortnotes import create_app
from vortnotes.webapp import ensure_db_initialized, resolve_db_path, set_db_password, set_db_read_without_password


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
