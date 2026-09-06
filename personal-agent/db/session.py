# db/session.py
import sqlite3
import json
import time
from typing import Optional, Dict, Any

DB_PATH = "agent_session.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database tables."""
    conn = get_db()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_emails (
                email_id TEXT PRIMARY KEY,
                seen_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                chat_id TEXT NOT NULL,
                action TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)

# --- Pending Actions (Inline confirmation) ---

def save_pending_action(action_id: str, chat_id: str, action_type: str, payload: dict, ttl_seconds: int = 600):
    now = time.time()
    expires_at = now + ttl_seconds
    conn = get_db()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_actions (id, chat_id, action_type, payload, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (action_id, str(chat_id), action_type, json.dumps(payload), now, expires_at)
        )

def get_pending_action(action_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,))
    row = cursor.fetchone()
    if not row:
        return None
    if time.time() > row["expires_at"]:
        delete_pending_action(action_id)
        return None
    return {
        "id": row["id"],
        "chat_id": row["chat_id"],
        "action_type": row["action_type"],
        "payload": json.loads(row["payload"]),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"]
    }

def delete_pending_action(action_id: str):
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM pending_actions WHERE id = ?", (action_id,))

# --- Seen Emails ---

def mark_email_seen(email_id: str):
    conn = get_db()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_emails (email_id, seen_at) VALUES (?, ?)",
            (email_id, time.time())
        )

def is_email_seen(email_id: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM seen_emails WHERE email_id = ?", (email_id,))
    return cursor.fetchone() is not None

# --- Conversation History ---

def add_message(chat_id: str, role: str, content: str):
    conn = get_db()
    with conn:
        conn.execute(
            "INSERT INTO conversation_history (chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (str(chat_id), role, content, time.time())
        )

def get_history(chat_id: str, limit: int = 10) -> list[dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM conversation_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
        (str(chat_id), limit)
    )
    rows = cursor.fetchall()
    history = [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
    return history

# Initialize DB on import
init_db()
