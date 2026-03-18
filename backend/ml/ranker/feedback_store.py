"""
SQLite store for POI feedback signals.
Logs thumbs up/down to disk for incremental ranker retraining.
"""

import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "feedback.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poi_id INTEGER NOT NULL,
            poi_name TEXT,
            category TEXT,
            goals TEXT,
            relevant INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def log_feedback(poi_id: int, relevant: bool, poi_name: str = "", category: str = "", goals: list[str] = None) -> None:
    """Store a feedback signal."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO feedback (poi_id, poi_name, category, goals, relevant, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (poi_id, poi_name, category, ",".join(goals or []), int(relevant), datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def get_feedback_count() -> int:
    """Return total number of feedback signals collected."""
    conn = _get_conn()
    count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    conn.close()
    return count


def get_feedback_as_training_data() -> list[dict]:
    """Return all feedback signals as structured dicts for retraining."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT poi_id, poi_name, category, goals, relevant FROM feedback"
    ).fetchall()
    conn.close()
    return [
        {"poi_id": r[0], "poi_name": r[1], "category": r[2],
         "goals": r[3].split(","), "relevant": bool(r[4])}
        for r in rows
    ]
