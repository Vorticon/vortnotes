from vortnotes import create_app


def test_healthz_ok():
    app = create_app()
    client = app.test_client()
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
