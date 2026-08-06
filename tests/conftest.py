import importlib
import os
import shutil
import sqlite3
import tempfile
import secrets

import pytest


@pytest.fixture(scope="session")
def app_module():
    test_root = tempfile.mkdtemp(prefix="cctv-pytest-")
    os.environ.update(
        CCTV_SECRET_KEY="test-secret-key-for-cctv-auth-suite-0123456789",
        CCTV_BOOTSTRAP_USERNAME="admin",
        CCTV_BOOTSTRAP_PASSWORD="BootstrapPass1",
        CCTV_COOKIE_SECURE="0",
        VIDEO_DIR=os.path.join(test_root, "media"),
        CCTV_STATE_FILE=os.path.join(test_root, "alerts_enabled"),
        CCTV_SNAPSHOT_PATH=os.path.join(test_root, "snapshot.jpg"),
        EVENT_DB_PATH=os.path.join(test_root, "events.db"),
    )
    os.makedirs(os.environ["VIDEO_DIR"], exist_ok=True)

    app_module = importlib.import_module("app")
    app_module.app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    app_module.initialize_security()
    try:
        yield app_module
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


@pytest.fixture(scope="session")
def app(app_module):
    return app_module.app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app_module):
    app_module.ensure_db(app_module.VIDEO_DIR)
    conn = sqlite3.connect(os.environ["EVENT_DB_PATH"])
    try:
        # isolation: clear mutable tables before each test
        conn.execute("DELETE FROM audit_log")
        conn.execute("DELETE FROM notifications")
        conn.execute("DELETE FROM notification_prefs")
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM users")
        conn.commit()
    finally:
        conn.close()
    yield


@pytest.fixture
def admin_user(db, app_module):
    app_module.create_user(
        app_module.VIDEO_DIR,
        username="admin",
        password="AdminPass1",
        role="admin",
        display_name="Admin",
        must_change_password=False,
    )
    return {"username": "admin", "password": "AdminPass1"}


@pytest.fixture
def viewer_user(db, app_module):
    app_module.create_user(
        app_module.VIDEO_DIR,
        username="viewer",
        password="ViewerPass1",
        role="viewer",
        display_name="Viewer",
        must_change_password=False,
    )
    return {"username": "viewer", "password": "ViewerPass1"}


def _login(client, username, password, next_path=None):
    path = "/login"
    if next_path:
        path = f"/login?next={next_path}"
    client.environ_base["REMOTE_ADDR"] = f"198.51.100.{secrets.randbelow(250) + 1}"
    return client.post(path, data={"username": username, "password": password})


@pytest.fixture
def authed_admin(client, admin_user):
    response = _login(client, admin_user["username"], admin_user["password"])
    assert response.status_code == 302
    return client


@pytest.fixture
def authed_viewer(client, viewer_user):
    response = _login(client, viewer_user["username"], viewer_user["password"])
    assert response.status_code == 302
    return client


@pytest.fixture
def csrf_token():
    def _csrf(test_client):
        with test_client.session_transaction() as state:
            return state["csrf_token"]

    return _csrf
