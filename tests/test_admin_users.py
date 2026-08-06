import os
import sqlite3


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


def test_admin_users_page_requires_admin(app_module, client, authed_viewer):
    anon_client = app_module.app.test_client()
    assert anon_client.get("/admin/users").status_code == 302
    # Viewer logged in => explicit permission denied
    assert authed_viewer.get("/admin/users").status_code == 403


def test_admin_can_view_user_management(authed_admin):
    response = authed_admin.get("/admin/users")
    assert response.status_code == 200
    assert b"User management" in response.data


def test_admin_can_add_user(authed_admin, csrf_token, app_module):
    response = authed_admin.post(
        "/admin/users",
        data={
            "csrf_token": csrf_token(authed_admin),
            "action": "add",
            "username": "mom",
            "display_name": "Mom",
            "role": "viewer",
            "temp_password": "MomPass123",
        },
    )
    assert response.status_code == 200
    user = _user_row("mom")
    assert user is not None
    assert user["role"] == "viewer"


def test_admin_can_deactivate_and_reactivate_user(authed_admin, viewer_user, csrf_token, app_module):
    viewer = _user_row(viewer_user["username"])
    deact = authed_admin.post(
        "/admin/users",
        data={"csrf_token": csrf_token(authed_admin), "action": "deactivate", "user_id": str(viewer["id"])},
    )
    assert deact.status_code == 200
    updated = _user_row(viewer_user["username"])
    assert updated["is_active"] == 0

    react = authed_admin.post(
        "/admin/users",
        data={"csrf_token": csrf_token(authed_admin), "action": "reactivate", "user_id": str(viewer["id"])},
    )
    assert react.status_code == 200
    updated2 = _user_row(viewer_user["username"])
    assert updated2["is_active"] == 1


def test_admin_password_reset_forces_change(authed_admin, viewer_user, csrf_token, app_module):
    viewer = _user_row(viewer_user["username"])
    response = authed_admin.post(
        "/admin/users",
        data={
            "csrf_token": csrf_token(authed_admin),
            "action": "reset_password",
            "user_id": str(viewer["id"]),
            "temp_password": "ViewerReset1",
        },
    )
    assert response.status_code == 200
    relogin = app_module.authenticate_user(app_module.VIDEO_DIR, "viewer", "ViewerReset1")
    assert relogin is not None
    assert relogin["must_change_password"] is True


def test_admin_cannot_modify_self(authed_admin, csrf_token):
    admin = _user_row("admin")

    deact = authed_admin.post(
        "/admin/users",
        data={"csrf_token": csrf_token(authed_admin), "action": "deactivate", "user_id": str(admin["id"])},
    )
    assert deact.status_code == 200
    assert b"You cannot deactivate your own account." in deact.data

    delete = authed_admin.post(
        "/admin/users",
        data={"csrf_token": csrf_token(authed_admin), "action": "delete", "user_id": str(admin["id"])},
    )
    assert delete.status_code == 200
    assert b"You cannot delete your own account." in delete.data


def test_admin_rejects_invalid_action(authed_admin, csrf_token):
    response = authed_admin.post(
        "/admin/users",
        data={"csrf_token": csrf_token(authed_admin), "action": "unknown"},
    )
    assert response.status_code == 200
    assert b"Invalid admin action." in response.data


def test_admin_rejects_weak_password_on_add(authed_admin, csrf_token):
    response = authed_admin.post(
        "/admin/users",
        data={
            "csrf_token": csrf_token(authed_admin),
            "action": "add",
            "username": "weak",
            "display_name": "Weak",
            "role": "viewer",
            "temp_password": "short",
        },
    )
    assert response.status_code == 200
    assert b"Password must be at least 8 characters." in response.data


def test_admin_can_delete_user(authed_admin, viewer_user, csrf_token, app_module):
    viewer = _user_row(viewer_user["username"])
    response = authed_admin.post(
        "/admin/users",
        data={"csrf_token": csrf_token(authed_admin), "action": "delete", "user_id": str(viewer["id"])},
    )
    assert response.status_code == 200
    assert _user_row(viewer_user["username"]) is None
