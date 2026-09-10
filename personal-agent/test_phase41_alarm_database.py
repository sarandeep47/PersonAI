import os
import time
import uuid
import sqlite3
import pytest
import db.session as db

@pytest.fixture(autouse=True)
def setup_temp_db(tmp_path, monkeypatch):
    """Use a temporary database file for all tests in this module."""
    test_db_path = str(tmp_path / "test_alarm_session.db")
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_db()
    yield test_db_path


def test_01_alarms_table_created(setup_temp_db):
    """1. Verify that the alarms table is created in SQLite database."""
    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alarms'")
    row = cursor.fetchone()
    assert row is not None
    assert row["name"] == "alarms"

    # Verify table schema columns
    cursor.execute("PRAGMA table_info(alarms)")
    columns = {col["name"]: col["type"] for col in cursor.fetchall()}
    assert "id" in columns
    assert "chat_id" in columns
    assert "message" in columns
    assert "fire_at" in columns
    assert "fired" in columns
    assert "created_at" in columns


def test_02_save_alarm(setup_temp_db):
    """2. Save an alarm and verify stored values."""
    chat_id = "user_123"
    message = "Water plants"
    fire_at = time.time() + 3600
    created_at = time.time()

    alarm_id = db.save_alarm(chat_id=chat_id, message=message, fire_at=fire_at, created_at=created_at)
    assert alarm_id is not None
    assert isinstance(alarm_id, str)

    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,))
    row = cursor.fetchone()
    assert row is not None
    assert row["id"] == alarm_id
    assert row["chat_id"] == chat_id
    assert row["message"] == message
    assert pytest.approx(row["fire_at"]) == fire_at
    assert row["fired"] == 0
    assert pytest.approx(row["created_at"]) == created_at


def test_03_retrieve_pending_alarms(setup_temp_db):
    """3. Retrieve pending alarms."""
    chat_id = "user_456"
    fire_at = time.time() + 1800
    alarm_id = db.save_alarm(chat_id=chat_id, message="Call mom", fire_at=fire_at)

    pending = db.get_pending_alarms(chat_id=chat_id)
    assert len(pending) == 1
    assert pending[0]["id"] == alarm_id
    assert pending[0]["message"] == "Call mom"
    assert pending[0]["fired"] == 0

    # Also test global get_pending_alarms without chat_id
    all_pending = db.get_pending_alarms()
    assert len(all_pending) >= 1
    assert any(a["id"] == alarm_id for a in all_pending)


def test_04_multiple_alarms_ordered_by_fire_at(setup_temp_db):
    """4. Verify multiple alarms are ordered by fire_at ascending."""
    chat_id = "user_789"
    now = time.time()

    # Save out of chronological order
    id3 = db.save_alarm(chat_id=chat_id, message="Third alarm", fire_at=now + 300)
    id1 = db.save_alarm(chat_id=chat_id, message="First alarm", fire_at=now + 100)
    id2 = db.save_alarm(chat_id=chat_id, message="Second alarm", fire_at=now + 200)

    pending = db.get_pending_alarms(chat_id=chat_id)
    assert len(pending) == 3
    assert pending[0]["id"] == id1
    assert pending[1]["id"] == id2
    assert pending[2]["id"] == id3


def test_05_fired_alarms_excluded_from_pending(setup_temp_db):
    """5. Verify fired alarms are excluded from pending alarms."""
    chat_id = "user_fired_test"
    now = time.time()

    id_pending = db.save_alarm(chat_id=chat_id, message="Active alarm", fire_at=now + 100)
    id_fired = db.save_alarm(chat_id=chat_id, message="Done alarm", fire_at=now + 50)

    # Mark id_fired as fired
    success = db.mark_alarm_fired(id_fired)
    assert success is True

    pending = db.get_pending_alarms(chat_id=chat_id)
    assert len(pending) == 1
    assert pending[0]["id"] == id_pending


def test_06_mark_alarm_fired(setup_temp_db):
    """6. Mark an alarm as fired and verify status update."""
    chat_id = "user_mark_test"
    alarm_id = db.save_alarm(chat_id=chat_id, message="Take medication", fire_at=time.time() + 60)

    res = db.mark_alarm_fired(alarm_id)
    assert res is True

    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT fired FROM alarms WHERE id = ?", (alarm_id,))
    row = cursor.fetchone()
    assert row["fired"] == 1

    # Marking non-existent alarm returns False
    res_non_existent = db.mark_alarm_fired("non_existent_id")
    assert res_non_existent is False


def test_07_list_alarms(setup_temp_db):
    """7. Test listing alarms for a chat_id with include_fired parameter."""
    chat_id = "user_list_test"
    now = time.time()

    id1 = db.save_alarm(chat_id=chat_id, message="Pending 1", fire_at=now + 100)
    id2 = db.save_alarm(chat_id=chat_id, message="Pending 2", fire_at=now + 200)
    db.mark_alarm_fired(id1)

    # Default include_fired=False
    pending_list = db.list_alarms(chat_id=chat_id)
    assert len(pending_list) == 1
    assert pending_list[0]["id"] == id2

    # include_fired=True
    all_list = db.list_alarms(chat_id=chat_id, include_fired=True)
    assert len(all_list) == 2
    assert [a["id"] for a in all_list] == [id1, id2]


def test_08_delete_alarm(setup_temp_db):
    """8. Delete an alarm by ID."""
    chat_id = "user_del_test"
    alarm_id = db.save_alarm(chat_id=chat_id, message="Delete me", fire_at=time.time() + 100)

    deleted = db.delete_alarm(alarm_id)
    assert deleted is True

    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,))
    assert cursor.fetchone() is None

    # Deleting already deleted alarm returns False
    assert db.delete_alarm(alarm_id) is False


def test_09_deleting_one_alarm_does_not_delete_another(setup_temp_db):
    """9. Verify deleting one alarm leaves other alarms intact."""
    chat_id = "user_isolation_test"
    now = time.time()

    id1 = db.save_alarm(chat_id=chat_id, message="Keep me", fire_at=now + 100)
    id2 = db.save_alarm(chat_id=chat_id, message="Delete me", fire_at=now + 200)

    deleted = db.delete_alarm(id2)
    assert deleted is True

    pending = db.list_alarms(chat_id=chat_id)
    assert len(pending) == 1
    assert pending[0]["id"] == id1
    assert pending[0]["message"] == "Keep me"


def test_10_alarm_data_persists_correctly(setup_temp_db):
    """10. Verify alarm data persists across new database connections."""
    chat_id = "user_persist_test"
    custom_id = str(uuid.uuid4())
    fire_at = 1750000000.0
    created_at = 1740000000.0
    message = "Persisted alarm message"

    db.save_alarm(chat_id=chat_id, message=message, fire_at=fire_at, alarm_id=custom_id, created_at=created_at)

    # Open a completely fresh raw SQLite connection to test_db_path
    raw_conn = sqlite3.connect(setup_temp_db)
    raw_conn.row_factory = sqlite3.Row
    cursor = raw_conn.cursor()
    cursor.execute("SELECT * FROM alarms WHERE id = ?", (custom_id,))
    row = cursor.fetchone()
    raw_conn.close()

    assert row is not None
    assert row["id"] == custom_id
    assert row["chat_id"] == chat_id
    assert row["message"] == message
    assert row["fire_at"] == fire_at
    assert row["fired"] == 0
    assert row["created_at"] == created_at
