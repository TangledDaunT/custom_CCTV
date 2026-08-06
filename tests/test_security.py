def test_dashboard_events_and_video_require_login(client):
    assert client.get("/").status_code == 302
    assert client.get("/health").status_code == 302
    assert client.get("/events").status_code == 302
    assert client.get("/events/1/video").status_code == 302
    assert client.get("/static/app.css").status_code == 200


def test_control_requires_login(client):
    assert client.post("/alerts", json={"enabled": False}).status_code == 302


def test_control_requires_csrf_when_logged_in(authed_admin, csrf_token):
    assert authed_admin.post("/alerts", json={"enabled": False}).status_code == 400
    assert (
        authed_admin.post(
            "/alerts",
            json={"enabled": False},
            headers={"X-CSRF-Token": csrf_token(authed_admin)},
        ).status_code
        == 200
    )


def test_unavailable_object_model_never_confirms_motion(app_module):
    assert app_module.detect_person_vehicle(None, None) == []


def test_must_change_password_redirects_after_login(db, app_module, client):
    app_module.create_user(
        app_module.VIDEO_DIR,
        username="viewer1",
        password="Viewer123",
        role="viewer",
        display_name="Viewer One",
        must_change_password=True,
    )
    client.environ_base["REMOTE_ADDR"] = "198.51.100.77"
    response = client.post("/login", data={"username": "viewer1", "password": "Viewer123"})
    assert response.status_code == 302
    assert "/change-password" in response.headers.get("Location", "")


def test_login_rate_limit_returns_429_when_enabled(app_module):
    previous = app_module.app.config.get("RATELIMIT_ENABLED", False)
    app_module.app.config["RATELIMIT_ENABLED"] = True
    client = app_module.app.test_client()
    client.environ_base["REMOTE_ADDR"] = "203.0.113.10"

    try:
        for _ in range(10):
            response = client.post("/login", data={"username": "missing", "password": "WrongPass1"})
            assert response.status_code == 200

        limited = client.post("/login", data={"username": "missing", "password": "WrongPass1"})
        assert limited.status_code == 429
        assert b"Too many attempts" in limited.data
    finally:
        app_module.app.config["RATELIMIT_ENABLED"] = previous


def test_cookie_and_session_settings_are_test_safe(app_module):
    app = app_module.app
    assert app.config["SESSION_COOKIE_SECURE"] is False
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
