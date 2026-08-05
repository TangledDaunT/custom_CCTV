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
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                video_path TEXT NOT NULL,
                thumb_path TEXT,
                score REAL NOT NULL DEFAULT 0,
                reviewed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('viewer', 'operator', 'admin')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                username TEXT,
                action TEXT NOT NULL,
                detail TEXT
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        if "reviewed_at" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN reviewed_at TEXT")
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


def list_events(default_dir: str, limit: int = 50, offset: int = 0):
    if not os.path.exists(get_db_path(default_dir)):
        return []
    conn = _connection(default_dir)
    try:
        rows = conn.execute(
            "SELECT id, ts, video_path, thumb_path, score, reviewed_at "
            "FROM events ORDER BY id DESC LIMIT ? OFFSET ?",
            (min(max(int(limit), 1), 100), max(int(offset), 0)),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def event_by_id(default_dir: str, event_id: int):
    conn = _connection(default_dir)
    try:
        row = conn.execute(
            "SELECT id, ts, video_path, thumb_path, score, reviewed_at FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def user_count(default_dir: str) -> int:
    conn = _connection(default_dir)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    finally:
        conn.close()


def create_user(default_dir: str, username: str, password: str, role: str = "admin", allow_weak_password: bool = False):
    username = username.strip()
    if not username or len(username) > 64:
        raise ValueError("Username must be between 1 and 64 characters.")
    if len(password) < 12 and not allow_weak_password:
        raise ValueError("Password must be at least 12 characters.")
    if len(password) < 5:
        raise ValueError("Password must be at least 5 characters.")
    if role not in {"viewer", "operator", "admin"}:
        raise ValueError("Invalid role.")
    conn = _connection(default_dir)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password, method="scrypt"), role, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def authenticate_user(default_dir: str, username: str, password: str):
    conn = _connection(default_dir)
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role, active FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()
        if not row or not row["active"] or not check_password_hash(row["password_hash"], password):
            return None
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), row["id"]))
        conn.commit()
        return {"id": row["id"], "username": row["username"], "role": row["role"]}
    finally:
        conn.close()


def audit(default_dir: str, username: Optional[str], action: str, detail: Optional[str] = None):
    conn = _connection(default_dir)
    try:
        conn.execute("INSERT INTO audit_log (ts, username, action, detail) VALUES (?, ?, ?, ?)", (_now(), username, action, detail))
        conn.commit()
    finally:
        conn.close()
