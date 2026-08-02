import sqlite3
import os
from datetime import datetime

DB_FILE_ENV = "EVENT_DB_PATH"

def get_db_path(default_dir):
    return os.environ.get(DB_FILE_ENV, os.path.join(default_dir, "events.db"))

def ensure_db(default_dir):
    path = get_db_path(default_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            video_path TEXT NOT NULL,
            thumb_path TEXT,
            score REAL
        )
        """
    )
    conn.commit()
    conn.close()
    return path

def insert_event(default_dir, video_path, thumb_path=None, score=0.0):
    db = get_db_path(default_dir)
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    cur.execute(
        "INSERT INTO events (ts, video_path, thumb_path, score) VALUES (?, ?, ?, ?)",
        (ts, video_path, thumb_path, float(score)),
    )
    conn.commit()
    conn.close()

def list_events(default_dir, limit=50):
    db = get_db_path(default_dir)
    if not os.path.exists(db):
        return []
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT id, ts, video_path, thumb_path, score FROM events ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    return [dict(id=r[0], ts=r[1], video=r[2], thumb=r[3], score=r[4]) for r in rows]
