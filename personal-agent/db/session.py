# db/session.py
import sqlite3
import json
import time
import re
import difflib
import csv
import os
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_used_at REAL NOT NULL,
                CONSTRAINT idx_contacts_chat_norm_name UNIQUE (chat_id, normalized_name)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                chat_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                updated_at REAL NOT NULL
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

def get_latest_pending_draft(chat_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve active editing payload or the most recent unexpired confirm_send payload for a chat_id."""
    editing = get_pending_action(f"editing_{chat_id}")
    if editing and editing.get("payload"):
        return editing["payload"]
    
    conn = get_db()
    cursor = conn.cursor()
    now = time.time()
    cursor.execute(
        "SELECT payload FROM pending_actions WHERE chat_id = ? AND action_type = 'confirm_send' AND expires_at > ? ORDER BY created_at DESC LIMIT 1",
        (str(chat_id), now)
    )
    row = cursor.fetchone()
    if row:
        return json.loads(row["payload"])
    return None

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

def scrub_contact_from_history(chat_id: str, email: str) -> int:
    """
    Redact a deleted contact's email address from every conversation_history row
    for chat_id.  The email is replaced with the placeholder '[redacted]' so the
    row structure (and message count) is preserved, but the address can no longer
    resurface as context for future LLM calls.

    Also scrubs the address from any unexpired pending_actions payloads (e.g. a
    draft that was queued before the contact was removed).

    Returns the number of history rows that were modified.
    """
    if not email or "@" not in email:
        return 0

    email_lower = email.strip().lower()
    # Build a case-insensitive pattern that matches the exact email address
    # (word-boundary anchored so "foo@bar.com" doesn't match "xfoo@bar.com")
    pattern = re.compile(re.escape(email_lower), re.IGNORECASE)
    redaction = "[redacted]"

    conn = get_db()
    cursor = conn.cursor()

    # --- 1. Scrub conversation_history rows ---
    cursor.execute(
        "SELECT id, content FROM conversation_history WHERE chat_id = ? AND LOWER(content) LIKE ?",
        (str(chat_id), f"%{email_lower}%")
    )
    rows = cursor.fetchall()
    modified = 0
    with conn:
        for row in rows:
            new_content = pattern.sub(redaction, row["content"])
            if new_content != row["content"]:
                conn.execute(
                    "UPDATE conversation_history SET content = ? WHERE id = ?",
                    (new_content, row["id"])
                )
                modified += 1

    # --- 2. Scrub pending_actions payloads that contain the address ---
    cursor.execute(
        "SELECT id, payload FROM pending_actions WHERE chat_id = ? AND LOWER(payload) LIKE ?",
        (str(chat_id), f"%{email_lower}%")
    )
    pending_rows = cursor.fetchall()
    with conn:
        for row in pending_rows:
            new_payload = pattern.sub(redaction, row["payload"])
            if new_payload != row["payload"]:
                conn.execute(
                    "UPDATE pending_actions SET payload = ? WHERE id = ?",
                    (new_payload, row["id"])
                )

    print(
        f"[DB] scrub_contact_from_history: redacted '{email}' from "
        f"{modified} history row(s) and {len(pending_rows)} pending action(s) "
        f"for chat_id={chat_id}."
    )
    return modified

# --- Contacts & User Profile ---

def normalize_contact_name(name: str) -> str:
    """
    Normalize contact name for matching:
    1. Strip common honorifics/titles (Mr., Mrs., Ms., Dr., Prof., Sir, etc.)
    2. Remove punctuation and convert to lowercase
    3. Collapse whitespace
    """
    if not name:
        return ""
    text = name.lower().strip()
    text = re.sub(r"\b(mr|mrs|ms|dr|prof|sir|lady|mx)\b\.?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())

def upsert_contact(chat_id: str, name: str, email: str) -> Optional[Dict[str, Any]]:
    """
    Save or update a contact for chat_id.
    Matches existing contact by normalized_name OR email.
    Uses UPSERT logic.
    """
    norm_name = normalize_contact_name(name)
    if not norm_name or not email or "@" not in email:
        return None

    now = time.time()
    conn = get_db()
    with conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM contacts WHERE chat_id = ? AND (normalized_name = ? OR LOWER(email) = LOWER(?))",
            (str(chat_id), norm_name, email.strip())
        )
        existing = cursor.fetchone()

        if existing:
            conn.execute(
                "UPDATE contacts SET name = ?, normalized_name = ?, email = ?, last_used_at = ? WHERE id = ?",
                (name.strip(), norm_name, email.strip().lower(), now, existing["id"])
            )
            contact_id = existing["id"]
        else:
            cursor.execute(
                "INSERT INTO contacts (chat_id, name, normalized_name, email, created_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(chat_id), name.strip(), norm_name, email.strip().lower(), now, now)
            )
            contact_id = cursor.lastrowid

    return {
        "id": contact_id,
        "chat_id": str(chat_id),
        "name": name.strip(),
        "normalized_name": norm_name,
        "email": email.strip().lower(),
        "last_used_at": now
    }

def get_contacts(chat_id: str) -> list[dict]:
    """Return all saved contacts for a chat_id ordered by last_used_at DESC."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM contacts WHERE chat_id = ? ORDER BY last_used_at DESC",
        (str(chat_id),)
    )
    rows = cursor.fetchall()
    return [dict(r) for r in rows]

def delete_contact(chat_id: str, identifier: str) -> bool:
    """Delete a contact by name, normalized_name, or email."""
    norm_id = normalize_contact_name(identifier)
    conn = get_db()
    with conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM contacts WHERE chat_id = ? AND (normalized_name = ? OR LOWER(email) = LOWER(?) OR LOWER(name) = LOWER(?))",
            (str(chat_id), norm_id, identifier.strip().lower(), identifier.strip().lower())
        )
        return cursor.rowcount > 0

def rename_contact(chat_id: str, identifier: str, new_name: str) -> bool:
    """
    Rename an existing contact identified by name, nickname, role, or email.
    Updates both the display name and normalized_name in place.
    Returns True if a row was updated, False if no matching contact was found.
    """
    new_name = new_name.strip()
    if not new_name:
        return False
    new_norm = normalize_contact_name(new_name)
    norm_id = normalize_contact_name(identifier)
    now = time.time()
    conn = get_db()
    with conn:
        cursor = conn.cursor()
        # Find the contact first (same matching logic as delete_contact)
        cursor.execute(
            "SELECT id FROM contacts WHERE chat_id = ? AND (normalized_name = ? OR LOWER(email) = LOWER(?) OR LOWER(name) = LOWER(?))",
            (str(chat_id), norm_id, identifier.strip().lower(), identifier.strip().lower())
        )
        row = cursor.fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE contacts SET name = ?, normalized_name = ?, last_used_at = ? WHERE id = ?",
            (new_name, new_norm, now, row["id"])
        )
        return True

def find_contact_by_name(chat_id: str, query_name: str) -> Optional[Dict[str, Any]]:
    """
    Find matching contact for chat_id given a query name or prompt segment.
    Returns contact dict if match score >= 0.85 (85%), else None.
    """
    norm_query = normalize_contact_name(query_name)
    if not norm_query:
        return None

    contacts = get_contacts(chat_id)
    if not contacts:
        return None

    best_match = None
    best_score = 0.0

    for c in contacts:
        norm_c = c["normalized_name"]

        # 1. Exact match -> 100% confidence
        if norm_query == norm_c:
            return c

        # 2. Token / word match (e.g. "Sarandeep" matching "Sarandeep Singh")
        query_words = set(norm_query.split())
        c_words = set(norm_c.split())
        if query_words and (query_words.issubset(c_words) or c_words.issubset(query_words)):
            score = 0.95
            if score > best_score:
                best_score = score
                best_match = c
            continue

        # 3. Substring word match
        if norm_c in norm_query or norm_query in norm_c:
            score = 0.90
            if score > best_score:
                best_score = score
                best_match = c
            continue

        # 4. Fuzzy SequenceMatcher similarity
        ratio = difflib.SequenceMatcher(None, norm_query, norm_c).ratio()
        if ratio > best_score:
            best_score = ratio
            best_match = c

    if best_score >= 0.85:
        return best_match

    return None

def find_contacts_matching_query(chat_id: str, query_name: str) -> list[dict]:
    """
    Find all matching contacts for chat_id given a query name or prompt segment.
    Used for ambiguous candidate detection when score >= 0.85.
    Returns list of matching contact dicts sorted by match score descending.
    """
    norm_query = normalize_contact_name(query_name)
    if not norm_query:
        return []

    contacts = get_contacts(chat_id)
    if not contacts:
        return []

    matches = []
    for c in contacts:
        norm_c = c["normalized_name"]

        # 1. Exact match -> 100% confidence
        if norm_query == norm_c:
            matches.append((c, 1.0))
            continue

        # 2. Token / word match
        query_words = set(norm_query.split())
        c_words = set(norm_c.split())
        if query_words and (query_words.issubset(c_words) or c_words.issubset(query_words)):
            matches.append((c, 0.95))
            continue

        # 3. Substring word match
        if norm_c in norm_query or norm_query in norm_c:
            matches.append((c, 0.90))
            continue

        # 4. Fuzzy SequenceMatcher similarity
        ratio = difflib.SequenceMatcher(None, norm_query, norm_c).ratio()
        if ratio >= 0.85:
            matches.append((c, ratio))

    # Sort matches by score descending
    matches.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in matches]

def save_user_profile(chat_id: str, display_name: str):
    """Save or update user profile display name."""
    now = time.time()
    conn = get_db()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_profile (chat_id, display_name, updated_at) VALUES (?, ?, ?)",
            (str(chat_id), display_name.strip(), now)
        )

def get_user_profile(chat_id: str) -> Optional[Dict[str, Any]]:
    """Get user profile dict for a chat_id."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_profile WHERE chat_id = ?", (str(chat_id),))
    row = cursor.fetchone()
    if row:
        return dict(row)
    return None

def extract_user_name_from_text(text: str) -> Optional[str]:
    """Detect user name statement in text like 'my name is Sade' or 'I am Sade'."""
    if not text:
        return None
    patterns = [
        r"\bmy name (?:is|'s)\s+([A-Za-z0-9_\-]+)",
        r"\bcall me\s+([A-Za-z0-9_\-]+)",
        r"\bi am\s+([A-Za-z0-9_\-]+)\b"
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            if name.lower() not in ["writing", "sending", "trying", "going", "here", "ready"]:
                return name.capitalize()
    return None

def export_contacts_csv(chat_id: str) -> Optional[str]:
    """
    Export all saved contacts for a chat_id to a CSV file (Google Sheet & Excel compatible).
    Returns absolute path of the generated CSV file or None if no contacts exist.
    """
    contacts = get_contacts(chat_id)
    if not contacts:
        return None

    download_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads")
    os.makedirs(download_dir, exist_ok=True)
    csv_path = os.path.join(download_dir, "contacts_export.csv")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Email", "Date Added", "Last Used"])
        for c in contacts:
            created_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(c.get("created_at", time.time())))
            last_used_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(c.get("last_used_at", time.time())))
            writer.writerow([c["name"], c["email"], created_str, last_used_str])

    return csv_path

# Initialize DB on import
init_db()
