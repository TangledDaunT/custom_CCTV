"""Fast regression tests for the privacy boundary of the web application."""

import os
import shutil
import tempfile
import unittest


TEST_ROOT = tempfile.mkdtemp(prefix="cctv-tests-")
os.environ.update(
    CCTV_SECRET_KEY="a" * 64,
    CCTV_BOOTSTRAP_PASSWORD="test-password-123",
    CCTV_COOKIE_SECURE="0",
    VIDEO_DIR=os.path.join(TEST_ROOT, "media"),
    CCTV_STATE_FILE=os.path.join(TEST_ROOT, "alerts_enabled"),
    CCTV_SNAPSHOT_PATH=os.path.join(TEST_ROOT, "snapshot.jpg"),
)

import app  # noqa: E402


class SecurityBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.initialize_security()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def setUp(self):
        self.client = app.app.test_client()

    def login(self):
        response = self.client.post("/login", data={"username": "admin", "password": "test-password-123"})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as state:
            return state["csrf_token"]

    def test_dashboard_events_and_video_require_login(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/events").status_code, 302)
        self.assertEqual(self.client.get("/events/1/video").status_code, 302)

    def test_control_requires_login_and_csrf(self):
        self.assertEqual(self.client.post("/alerts", json={"enabled": False}).status_code, 401)
        csrf = self.login()
        self.assertEqual(self.client.post("/alerts", json={"enabled": False}).status_code, 400)
        self.assertEqual(
            self.client.post("/alerts", json={"enabled": False}, headers={"X-CSRF-Token": csrf}).status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
