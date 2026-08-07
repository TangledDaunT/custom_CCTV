import os
import sqlite3
from pathlib import Path

import numpy as np


def _seed_event(app_module):
    media_dir = Path(app_module.VIDEO_DIR)
    media_dir.mkdir(parents=True, exist_ok=True)
    video = media_dir / "event_seed.mp4"
    thumb = media_dir / "event_seed_thumb.jpg"
    video.write_bytes(b"fake-mp4-bytes")
    thumb.write_bytes(b"fake-jpg-bytes")
    app_module.insert_event(app_module.VIDEO_DIR, str(video), str(thumb), 0.42)
    conn = sqlite3.connect(os.environ["EVENT_DB_PATH"])
    try:
        return conn.execute("SELECT id FROM events ORDER BY id DESC LIMIT 1").fetchone()[0]
    finally:
        conn.close()


def _set_event_fields(event_id, **fields):
    conn = sqlite3.connect(os.environ["EVENT_DB_PATH"])
    try:
        sets = ", ".join(f"{name} = ?" for name in fields)
        conn.execute(f"UPDATE events SET {sets} WHERE id = ?", (*fields.values(), event_id))
        conn.commit()
    finally:
        conn.close()


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


def test_events_list_requires_auth(client):
    assert client.get("/events").status_code == 302


def test_viewer_can_list_events(authed_viewer, app_module):
    _seed_event(app_module)
    response = authed_viewer.get("/events?limit=10")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "video_url" in data[0]
    assert "video_download_url" in data[0]


def test_events_list_supports_filters(authed_admin, app_module):
    first_id = _seed_event(app_module)
    second_id = _seed_event(app_module)
    _set_event_fields(first_id, camera="cam1", label="person", flagged=1, ts="2026-08-06T01:00:00Z")
    _set_event_fields(second_id, camera="cam2", label="car", flagged=0, ts="2026-08-06T02:00:00Z")

    flagged = authed_admin.get("/events?flagged=1")
    assert flagged.status_code == 200
    flagged_rows = flagged.get_json()
    assert any(row["id"] == first_id for row in flagged_rows)

    camera = authed_admin.get("/events?camera=cam2")
    assert camera.status_code == 200
    camera_rows = camera.get_json()
    assert len(camera_rows) == 1
    assert camera_rows[0]["id"] == second_id

    label = authed_admin.get("/events?label=person")
    assert label.status_code == 200
    label_rows = label.get_json()
    assert len(label_rows) == 1
    assert label_rows[0]["id"] == first_id

    since = authed_admin.get("/events?since_ts=2026-08-06T01:30:00Z")
    assert since.status_code == 200
    since_rows = since.get_json()
    assert len(since_rows) == 1
    assert since_rows[0]["id"] == second_id


def test_admin_can_flag_and_filter_events(authed_admin, csrf_token, app_module):
    event_id = _seed_event(app_module)
    flag = authed_admin.post(
        f"/events/{event_id}/flag",
        json={"flagged": True},
        headers={"X-CSRF-Token": csrf_token(authed_admin)},
    )
    assert flag.status_code == 200
    assert flag.get_json()["flagged"] is True

    filtered = authed_admin.get("/events?flagged=1")
    assert filtered.status_code == 200
    rows = filtered.get_json()
    assert any(row["id"] == event_id for row in rows)


def test_viewer_cannot_flag_event(authed_viewer, csrf_token, app_module):
    event_id = _seed_event(app_module)
    response = authed_viewer.post(
        f"/events/{event_id}/flag",
        json={"flagged": True},
        headers={"X-CSRF-Token": csrf_token(authed_viewer)},
    )
    assert response.status_code == 403


def test_signed_share_url_works_and_expired_token_fails(authed_admin, csrf_token, app_module):
    event_id = _seed_event(app_module)
    share_resp = authed_admin.post(
        f"/events/{event_id}/share",
        json={"ttl": 3600},
        headers={"X-CSRF-Token": csrf_token(authed_admin)},
    )
    assert share_resp.status_code == 200
    share_url = share_resp.get_json()["share_url"]
    path = "/" + share_url.split("/", 3)[-1]
    ok = authed_admin.get(path)
    assert ok.status_code == 200

    expired_token = app_module._make_share_token(event_id, ttl_seconds=-1)
    expired = authed_admin.get(f"/shared/{expired_token}")
    assert expired.status_code == 404


def test_shared_token_rejects_tampering(authed_admin, csrf_token, app_module):
    event_id = _seed_event(app_module)
    share_resp = authed_admin.post(
        f"/events/{event_id}/share",
        json={"ttl": 3600},
        headers={"X-CSRF-Token": csrf_token(authed_admin)},
    )
    token = share_resp.get_json()["share_url"].rsplit("/", 1)[-1]
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    response = authed_admin.get(f"/shared/{tampered}")
    assert response.status_code == 404


def test_snapshot_requires_admin(authed_viewer, csrf_token):
    viewer_resp = authed_viewer.post(
        "/snapshot",
        headers={"X-CSRF-Token": csrf_token(authed_viewer)},
    )
    assert viewer_resp.status_code == 403


def test_snapshot_returns_503_without_frame_for_admin(authed_admin, csrf_token):
    admin_resp = authed_admin.post(
        "/snapshot",
        headers={"X-CSRF-Token": csrf_token(authed_admin)},
    )
    assert admin_resp.status_code == 503


def test_snapshot_saves_with_fake_frame(authed_admin, csrf_token, app_module):
    app_module._latest_bgr = np.zeros((24, 24, 3), dtype=np.uint8)

    response = authed_admin.post(
        "/snapshot",
        headers={"X-CSRF-Token": csrf_token(authed_admin)},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert "url" in data
    assert "download_url" in data
    saved = Path(app_module.VIDEO_DIR) / data["url"].rsplit("/", 1)[-1]
    assert saved.exists()

    downloaded = authed_admin.get(data["download_url"])
    assert downloaded.status_code == 200
    assert "attachment" in downloaded.headers["Content-Disposition"]


def test_event_media_downloads_require_login_and_are_attachments(authed_admin, app_module):
    event_id = _seed_event(app_module)
    anonymous = app_module.app.test_client()
    assert anonymous.get(f"/events/{event_id}/video?download=1").status_code == 302

    video = authed_admin.get(f"/events/{event_id}/video?download=1")
    thumbnail = authed_admin.get(f"/events/{event_id}/thumbnail?download=1")
    assert video.status_code == 200
    assert thumbnail.status_code == 200
    assert "attachment" in video.headers["Content-Disposition"]
    assert "attachment" in thumbnail.headers["Content-Disposition"]


def test_insert_event_persists_camera_and_detection_label(app_module):
    media_dir = Path(app_module.VIDEO_DIR)
    video = media_dir / "labelled.mp4"
    video.write_bytes(b"fake-mp4-bytes")
    event_id = app_module.insert_event(
        app_module.VIDEO_DIR, str(video), score=0.8, camera="front", label="person"
    )
    event = app_module.event_by_id(app_module.VIDEO_DIR, event_id)
    assert event["camera"] == "front"
    assert event["label"] == "person"


def test_notification_preferences_control_alert_recipients(db, app_module):
    original = list(app_module.ALERT_NUMBERS)
    try:
        app_module.ALERT_NUMBERS[:] = ["111", "222"]
        app_module.set_notification_pref(app_module.VIDEO_DIR, "111", False)
        assert app_module._enabled_alert_numbers() == ["222"]
    finally:
        app_module.ALERT_NUMBERS[:] = original


def test_hls_routes_are_private(client):
    assert client.get("/live/live.m3u8").status_code == 302


def test_alert_toggle_denied_for_viewer(authed_viewer, csrf_token):
    viewer_denied = authed_viewer.post(
        "/alerts",
        json={"enabled": False},
        headers={"X-CSRF-Token": csrf_token(authed_viewer)},
    )
    assert viewer_denied.status_code == 403


def test_alert_toggle_allowed_for_admin(authed_admin, csrf_token):
    admin_ok = authed_admin.post(
        "/alerts",
        json={"enabled": False},
        headers={"X-CSRF-Token": csrf_token(authed_admin)},
    )
    assert admin_ok.status_code == 200


def test_seed_users_cli_creates_family_accounts(db, app_module):
    runner = app_module.app.test_cli_runner()
    result = runner.invoke(args=["seed-users"])
    assert result.exit_code == 0
    out = result.output
    assert "Created users" in out or "No users created" in out
    assert _user_row("dad") is not None
    assert _user_row("mom") is not None
