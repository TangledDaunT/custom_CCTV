"""SQLite persistence for CCTV events and local user accounts.

The installation is intentionally single-host, so SQLite is a robust and low-cost
choice. Every connection is short-lived which keeps it safe with Flask threads.
"""

import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

DB_FILE_ENV = "EVENT_DB_PATH"


def get_db_path(default_dir: str) -> str:
    return os.environ.get(DB_FILE_ENV, os.path.join(default_dir, "events.db"))


def _connection(default_dir: str) -> sqlite3.Connection:
    path = get_db_path(default_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_db(default_dir: str) -> str:
    """Create or migrate the local schema without destroying existing events."""
    conn = _connection(default_dir)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            -- events: added flagged, camera and label to support filtering and multi-camera
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                video_path TEXT NOT NULL,
                thumb_path TEXT,
                score REAL NOT NULL DEFAULT 0,
                reviewed_at TEXT,
                flagged INTEGER NOT NULL DEFAULT 0,
                camera TEXT DEFAULT 'cam0',
                label TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('viewer', 'admin')),
                is_active INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                last_login TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                username TEXT,
                action TEXT NOT NULL,
                detail TEXT
            );
            -- notifications table (basic notification center/log)
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                sent_to TEXT,
                status TEXT,
                FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
            );
            -- notification preferences (per-recipient basic toggles)
            CREATE TABLE IF NOT EXISTS notification_prefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        if "reviewed_at" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN reviewed_at TEXT")
        if "flagged" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN flagged INTEGER NOT NULL DEFAULT 0")
        if "camera" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN camera TEXT DEFAULT 'cam0'")
        if "label" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN label TEXT")
        user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "display_name" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
        if "is_active" not in user_columns:
            if "active" in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
                conn.execute("UPDATE users SET is_active = COALESCE(active, 1)")
            else:
                conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "must_change_password" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
        if "last_login" not in user_columns:
            if "last_login_at" in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN last_login TEXT")
                conn.execute("UPDATE users SET last_login = last_login_at WHERE last_login IS NULL")
            else:
                conn.execute("ALTER TABLE users ADD COLUMN last_login TEXT")
        # Keep legacy role values valid after migration.
        conn.execute("UPDATE users SET role = 'admin' WHERE role NOT IN ('admin', 'viewer')")
        conn.execute("UPDATE users SET display_name = username WHERE display_name = '' OR display_name IS NULL")
        conn.commit()
        return get_db_path(default_dir)
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def insert_event(default_dir: str, video_path: str, thumb_path: Optional[str] = None, score: float = 0.0):
    conn = _connection(default_dir)
    try:
        conn.execute(
            "INSERT INTO events (ts, video_path, thumb_path, score) VALUES (?, ?, ?, ?)",
            (_now(), video_path, thumb_path, float(score)),
        )
        conn.commit()
    finally:
        conn.close()


def list_events(default_dir: str, limit: int = 50, offset: int = 0, filters: dict = None):
    """List events with optional filters.

    Supported filters keys:
      - camera: str
      - label: str ('person' or 'car' etc)
      - flagged: bool
      - since_ts: ISO string
    """
    if not os.path.exists(get_db_path(default_dir)):
        return []
    conn = _connection(default_dir)
    try:
        query = "SELECT id, ts, video_path, thumb_path, score, reviewed_at, flagged, camera, label FROM events"
        params = []
        where = []
        if filters:
            if 'camera' in filters and filters['camera']:
                where.append("camera = ?")
                params.append(filters['camera'])
            if 'label' in filters and filters['label']:
                where.append("label = ?")
                params.append(filters['label'])
            if 'flagged' in filters:
                where.append("flagged = ?")
                params.append(1 if filters['flagged'] else 0)
            if 'since_ts' in filters and filters['since_ts']:
                where.append("ts >= ?")
                params.append(filters['since_ts'])
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        params.extend([min(max(int(limit), 1), 100), max(int(offset), 0)])
        rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def event_by_id(default_dir: str, event_id: int):
    conn = _connection(default_dir)
    try:
        row = conn.execute(
            "SELECT id, ts, video_path, thumb_path, score, reviewed_at, flagged, camera, label FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def flag_event(default_dir: str, event_id: int, flagged: bool = True):
    conn = _connection(default_dir)
    try:
        conn.execute("UPDATE events SET flagged = ? WHERE id = ?", (1 if flagged else 0, event_id))
        conn.commit()
    finally:
        conn.close()


def insert_notification(default_dir: str, event_id: int, sent_to: str, status: str = 'sent'):
    conn = _connection(default_dir)
    try:
        conn.execute("INSERT INTO notifications (event_id, ts, sent_to, status) VALUES (?, ?, ?, ?)", (event_id, _now(), sent_to, status))
        conn.commit()
    finally:
        conn.close()


def set_notification_pref(default_dir: str, recipient: str, enabled: bool = True):
    conn = _connection(default_dir)
    try:
        cur = conn.execute("SELECT id FROM notification_prefs WHERE recipient = ?", (recipient,))
        row = cur.fetchone()
        if row:
            conn.execute("UPDATE notification_prefs SET enabled = ? WHERE recipient = ?", (1 if enabled else 0, recipient))
        else:
            conn.execute("INSERT INTO notification_prefs (recipient, enabled) VALUES (?, ?)", (recipient, 1 if enabled else 0))
        conn.commit()
    finally:
        conn.close()


def get_notification_prefs(default_dir: str):
    conn = _connection(default_dir)
    try:
        rows = conn.execute("SELECT recipient, enabled FROM notification_prefs").fetchall()
        return {row['recipient']: bool(row['enabled']) for row in rows}
    finally:
        conn.close()


def user_count(default_dir: str) -> int:
    conn = _connection(default_dir)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    finally:
        conn.close()


def _validate_password(password: str):
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if not any(ch.isdigit() for ch in password):
        raise ValueError("Password must include at least one number.")


def create_user(
    default_dir: str,
    username: str,
    password: str,
    role: str = "viewer",
    display_name: Optional[str] = None,
    must_change_password: bool = False,
):
    username = username.strip()
    if not username or len(username) > 64:
        raise ValueError("Username must be between 1 and 64 characters.")
    _validate_password(password)
    if role not in {"viewer", "admin"}:
        raise ValueError("Invalid role.")
    display_name = (display_name or username).strip()[:64]
    conn = _connection(default_dir)
    try:
        conn.execute(
            "INSERT INTO users (username, display_name, password_hash, role, is_active, must_change_password, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (username, display_name, generate_password_hash(password), role, 1 if must_change_password else 0, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def provision_single_admin(default_dir: str, username: str, password: str):
    """Create the initial administrator only when no users exist."""
    username = username.strip()
    if not username or len(username) > 64:
        raise ValueError("Username must be between 1 and 64 characters.")
    _validate_password(password)
    conn = _connection(default_dir)
    try:
        count = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        if count == 0:
            conn.execute(
                "INSERT INTO users (username, display_name, password_hash, role, is_active, must_change_password, created_at) "
                "VALUES (?, ?, ?, 'admin', 1, 0, ?)",
                (username, username, generate_password_hash(password), _now()),
            )
        conn.commit()
    finally:
        conn.close()


def authenticate_user(default_dir: str, username: str, password: str):
    conn = _connection(default_dir)
    try:
        row = conn.execute(
            "SELECT id, username, display_name, password_hash, role, is_active, must_change_password "
            "FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
        if not row or not row["is_active"] or not check_password_hash(row["password_hash"], password):
            return None
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), row["id"]))
        conn.commit()
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"] or row["username"],
            "role": row["role"],
            "must_change_password": bool(row["must_change_password"]),
        }
    finally:
        conn.close()


def list_users(default_dir: str):
    conn = _connection(default_dir)
    try:
        rows = conn.execute(
            "SELECT id, username, display_name, role, is_active, must_change_password, created_at, last_login "
            "FROM users ORDER BY role DESC, username ASC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_user_active(default_dir: str, user_id: int, is_active: bool):
    conn = _connection(default_dir)
    try:
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if is_active else 0, user_id))
        conn.commit()
    finally:
        conn.close()


def delete_user(default_dir: str, user_id: int):
    conn = _connection(default_dir)
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def reset_user_password(default_dir: str, user_id: int, new_password: str):
    _validate_password(new_password)
    conn = _connection(default_dir)
    try:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
            (generate_password_hash(new_password), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def change_own_password(default_dir: str, user_id: int, new_password: str):
    _validate_password(new_password)
    conn = _connection(default_dir)
    try:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (generate_password_hash(new_password), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_user(default_dir: str, user_id: int):
    conn = _connection(default_dir)
    try:
        row = conn.execute(
            "SELECT id, username, display_name, role, is_active, must_change_password, created_at, last_login "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_username(default_dir: str, username: str):
    conn = _connection(default_dir)
    try:
        row = conn.execute(
            "SELECT id, username, display_name, role, is_active, must_change_password, created_at, last_login "
            "FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def user_exists(default_dir: str, username: str) -> bool:
    return get_user_by_username(default_dir, username) is not None


def audit(default_dir: str, username: Optional[str], action: str, detail: Optional[str] = None):
    conn = _connection(default_dir)
    try:
        conn.execute("INSERT INTO audit_log (ts, username, action, detail) VALUES (?, ?, ?, ?)", (_now(), username, action, detail))
        conn.commit()
    finally:
        conn.close()
