# test_phase61_context_database.py
import pytest
import time
import os
import sqlite3
import db.session as session

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """
    Setup an isolated temporary database for Phase 6.1 testing.
    """
    db_file = str(tmp_path / "test_agent_session.db")
    monkeypatch.setattr(session, "DB_PATH", db_file)
    session.init_db()
    yield db_file

def test_01_context_entities_table_created():
    """Verify that context_entities table is properly initialized with required schema."""
    conn = session.get_db()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(context_entities)")
    columns = {row["name"]: row["type"] for row in cursor.fetchall()}

    assert "chat_id" in columns
    assert "entity_type" in columns
    assert "entity_id" in columns
    assert "title" in columns
    assert "details" in columns
    assert "updated_at" in columns

def test_02_save_and_retrieve_context():
    """Verify basic save and retrieve for calendar, task, and email entities."""
    res_cal = session.save_context_entity(
        chat_id="chat_1",
        entity_type="calendar",
        entity_id="event_101",
        title="Weekly Sync",
        details="Discuss roadmap"
    )
    assert res_cal is not None
    assert res_cal["chat_id"] == "chat_1"
    assert res_cal["entity_type"] == "calendar"
    assert res_cal["entity_id"] == "event_101"
    assert res_cal["title"] == "Weekly Sync"
    assert res_cal["details"] == "Discuss roadmap"
    assert isinstance(res_cal["updated_at"], float)

    retrieved = session.get_context_entity("chat_1", "calendar")
    assert retrieved is not None
    assert retrieved["chat_id"] == "chat_1"
    assert retrieved["entity_type"] == "calendar"
    assert retrieved["entity_id"] == "event_101"
    assert retrieved["title"] == "Weekly Sync"
    assert retrieved["details"] == "Discuss roadmap"

def test_03_latest_entity_replaces_previous_same_type():
    """Verify saving a new entity replaces the previous context entity of the same type."""
    t1 = time.time()
    session.save_context_entity("chat_1", "task", "task_01", "Initial Task", "Detail 1")
    
    time.sleep(0.01)
    
    session.save_context_entity("chat_1", "task", "task_02", "Updated Task", "Detail 2")

    retrieved = session.get_context_entity("chat_1", "task")
    assert retrieved is not None
    assert retrieved["entity_id"] == "task_02"
    assert retrieved["title"] == "Updated Task"
    assert retrieved["details"] == "Detail 2"
    assert retrieved["updated_at"] > t1

def test_04_independent_entity_types():
    """Verify calendar, task, and email context entities remain completely independent."""
    session.save_context_entity("chat_1", "calendar", "evt_1", "Calendar Event", "Cal Detail")
    session.save_context_entity("chat_1", "task", "task_1", "Task Title", "Task Detail")
    session.save_context_entity("chat_1", "email", "msg_1", "Email Subject", "Email Body")

    cal = session.get_context_entity("chat_1", "calendar")
    tsk = session.get_context_entity("chat_1", "task")
    eml = session.get_context_entity("chat_1", "email")

    assert cal["entity_id"] == "evt_1"
    assert tsk["entity_id"] == "task_1"
    assert eml["entity_id"] == "msg_1"

    # Update calendar entity - task and email should remain untouched
    session.save_context_entity("chat_1", "calendar", "evt_2", "New Event", "New Detail")

    cal_new = session.get_context_entity("chat_1", "calendar")
    tsk_still = session.get_context_entity("chat_1", "task")
    eml_still = session.get_context_entity("chat_1", "email")

    assert cal_new["entity_id"] == "evt_2"
    assert tsk_still["entity_id"] == "task_1"
    assert eml_still["entity_id"] == "msg_1"

def test_05_chat_isolation():
    """Verify that context entities are strictly isolated per chat_id."""
    session.save_context_entity("user_A", "calendar", "evt_A", "User A Event", "A Details")
    session.save_context_entity("user_B", "calendar", "evt_B", "User B Event", "B Details")

    ctx_a = session.get_context_entity("user_A", "calendar")
    ctx_b = session.get_context_entity("user_B", "calendar")
    ctx_c = session.get_context_entity("user_C", "calendar")

    assert ctx_a["entity_id"] == "evt_A"
    assert ctx_a["title"] == "User A Event"
    assert ctx_b["entity_id"] == "evt_B"
    assert ctx_b["title"] == "User B Event"
    assert ctx_c is None

def test_06_missing_context_returns_none():
    """Verify get_context_entity returns None safely for non-existent entities."""
    assert session.get_context_entity("non_existent_chat", "calendar") is None
    assert session.get_context_entity("chat_1", "non_existent_type") is None

def test_07_dict_and_none_details_handling():
    """Verify details handles dict objects (JSON serialized) and None correctly."""
    dict_details = {"room": "Conference 1", "attendees": ["alice", "bob"]}
    session.save_context_entity("chat_1", "calendar", "evt_json", "JSON Event", dict_details)

    retrieved = session.get_context_entity("chat_1", "calendar")
    assert retrieved is not None
    assert '"Conference 1"' in retrieved["details"]
    assert '"alice"' in retrieved["details"]

    session.save_context_entity("chat_1", "task", "task_no_details", "No Details Task", None)
    retrieved_no_details = session.get_context_entity("chat_1", "task")
    assert retrieved_no_details is not None
    assert retrieved_no_details["details"] is None

def test_08_db_error_resilience(monkeypatch):
    """Verify safe exception handling if database fails."""
    def broken_get_db():
        raise sqlite3.OperationalError("Database disk image is malformed")

    monkeypatch.setattr(session, "get_db", broken_get_db)

    assert session.save_context_entity("chat_1", "calendar", "e1", "t1") is None
    assert session.get_context_entity("chat_1", "calendar") is None
