import os
import sqlite3
import secrets


def _user_row(username):
    conn = sqlite3.connect(os.environ["EVENT_DB_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT id, username, role, is_active, must_change_password FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()


def _post_login(client, username, password, next_path=None):
    client.environ_base["REMOTE_ADDR"] = f"198.51.100.{secrets.randbelow(250) + 1}"
    path = "/login"
    if next_path:
        path = f"/login?next={next_path}"
    return client.post(path, data={"username": username, "password": password})


def test_bootstrap_admin_created_when_empty_db(db, app_module):
    # db fixture clears users; initialize should bootstrap admin again.
    app_module.initialize_security()
    user = app_module.authenticate_user(app_module.VIDEO_DIR, "admin", "BootstrapPass1")
    assert user is not None
    assert user["role"] == "admin"


def test_bootstrap_is_ignored_when_users_exist(db, app_module):
    app_module.create_user(
        app_module.VIDEO_DIR,
        username="existing",
        password="ExistingPass1",
        role="viewer",
        display_name="Existing",
        must_change_password=False,
    )

    app_module.initialize_security()

    assert _user_row("admin") is None
    assert _user_row("existing") is not None


def test_login_success_redirects_to_next(client, admin_user):
    response = _post_login(client, admin_user["username"], admin_user["password"], next_path="/events")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/events")


def test_login_rejects_unsafe_next_target(client, admin_user):
    response = _post_login(
        client,
        admin_user["username"],
        admin_user["password"],
        next_path="https://evil.example.com/phish",
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_login_failure_is_generic(client, admin_user):
    response = _post_login(client, admin_user["username"], "WrongPass1")
    assert response.status_code == 200
    assert b"Invalid credentials." in response.data


def test_login_rejects_inactive_user(db, app_module, client):
    app_module.create_user(
        app_module.VIDEO_DIR,
        username="inactive",
        password="InactivePass1",
        role="viewer",
        display_name="Inactive",
        must_change_password=False,
    )
    inactive = _user_row("inactive")
    conn = sqlite3.connect(os.environ["EVENT_DB_PATH"])
    try:
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (inactive["id"],))
        conn.commit()
    finally:
        conn.close()

    response = _post_login(client, "inactive", "InactivePass1")

    assert response.status_code == 200
    assert b"Invalid credentials." in response.data


def test_protected_route_redirects_to_login_with_next(client):
    response = client.get("/events")
    assert response.status_code == 302
    assert "/login?next=" in response.headers["Location"]


def test_logout_clears_session(authed_admin):
    response = authed_admin.get("/logout")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    with authed_admin.session_transaction() as state:
        assert "user_id" not in state


def test_change_password_flow(authed_admin, csrf_token, app_module):
    bad = authed_admin.post(
        "/change-password",
        data={
            "csrf_token": csrf_token(authed_admin),
            "new_password": "short",
            "confirm_password": "short",
        },
    )
    assert bad.status_code == 200
    assert b"at least 8 characters" in bad.data

    ok = authed_admin.post(
        "/change-password",
        data={
            "csrf_token": csrf_token(authed_admin),
            "new_password": "NewPass123",
            "confirm_password": "NewPass123",
        },
    )
    assert ok.status_code == 302
    assert ok.headers["Location"].endswith("/")
    assert app_module.authenticate_user(app_module.VIDEO_DIR, "admin", "NewPass123") is not None


def test_change_password_requires_matching_confirmation(authed_admin, csrf_token):
    response = authed_admin.post(
        "/change-password",
        data={
            "csrf_token": csrf_token(authed_admin),
            "new_password": "NewPass123",
            "confirm_password": "Different123",
        },
    )

    assert response.status_code == 200
    assert b"Passwords do not match." in response.data


def test_login_updates_last_login(admin_user, client):
    response = _post_login(client, "admin", "AdminPass1")
    assert response.status_code == 302
    conn = sqlite3.connect(os.environ["EVENT_DB_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT last_login FROM users WHERE username = 'admin'").fetchone()
        assert row["last_login"] is not None
    finally:
        conn.close()


def test_logout_post_requires_csrf(authed_admin):
    response = authed_admin.post("/logout")
    assert response.status_code == 400


def test_logout_post_accepts_csrf(authed_admin, csrf_token):
    response = authed_admin.post("/logout", data={"csrf_token": csrf_token(authed_admin)})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
