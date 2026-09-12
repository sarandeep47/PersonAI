import time
import pytest
import db.session as db

@pytest.fixture(autouse=True)
def setup_temp_db(tmp_path, monkeypatch):
    """Use a temporary database file for all tests in this module."""
    test_db_path = str(tmp_path / "test_task_session.db")
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_db()
    yield test_db_path


def test_01_tasks_table_created(setup_temp_db):
    """1. Verify that the tasks table is created in SQLite database."""
    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
    row = cursor.fetchone()
    assert row is not None
    assert row["name"] == "tasks"

    # Verify table schema columns
    cursor.execute("PRAGMA table_info(tasks)")
    columns = {col["name"]: col["type"] for col in cursor.fetchall()}
    assert "id" in columns
    assert "chat_id" in columns
    assert "title" in columns
    assert "done" in columns
    assert "created_at" in columns
    assert "done_at" in columns


def test_02_add_task(setup_temp_db):
    """2. Add a task and verify stored values."""
    chat_id = "chat_123"
    title = "Buy groceries"
    start_time = time.time()

    task = db.add_task(chat_id=chat_id, title=title)
    assert task is not None
    assert isinstance(task["id"], str)
    assert len(task["id"]) > 0
    assert task["chat_id"] == chat_id
    assert task["title"] == title
    assert task["done"] == 0
    assert task["created_at"] >= start_time
    assert task["done_at"] is None

    # Verify database query
    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],))
    row = cursor.fetchone()
    assert row is not None
    assert row["id"] == task["id"]
    assert row["chat_id"] == chat_id
    assert row["title"] == title
    assert row["done"] == 0
    assert row["done_at"] is None


def test_03_list_tasks(setup_temp_db):
    """3. List tasks and verify order (newest first)."""
    chat_id = "chat_456"

    task1 = db.add_task(chat_id=chat_id, title="Task 1")
    time.sleep(0.01)
    task2 = db.add_task(chat_id=chat_id, title="Task 2")
    time.sleep(0.01)
    task3 = db.add_task(chat_id=chat_id, title="Task 3")

    tasks = db.list_tasks(chat_id=chat_id)
    assert len(tasks) == 3
    # Newest created order (DESC)
    assert tasks[0]["id"] == task3["id"]
    assert tasks[1]["id"] == task2["id"]
    assert tasks[2]["id"] == task1["id"]


def test_04_empty_task_list(setup_temp_db):
    """4. Listing tasks for a chat with no tasks returns an empty list."""
    tasks = db.list_tasks(chat_id="nonexistent_chat")
    assert tasks == []


def test_05_complete_task(setup_temp_db):
    """5. Complete a task and verify done flag and timestamp."""
    chat_id = "chat_789"
    task = db.add_task(chat_id=chat_id, title="Complete report")

    assert task["done"] == 0
    assert task["done_at"] is None

    start_time = time.time()
    updated = db.complete_task(chat_id=chat_id, task_id=task["id"])

    assert updated is not None
    assert updated["id"] == task["id"]
    assert updated["done"] == 1
    assert updated["done_at"] is not None
    assert updated["done_at"] >= start_time


def test_06_complete_nonexistent_task(setup_temp_db):
    """6. Completing a nonexistent task returns None."""
    result = db.complete_task(chat_id="chat_123", task_id="nonexistent_id")
    assert result is None


def test_07_delete_task(setup_temp_db):
    """7. Delete a task and verify it is removed."""
    chat_id = "chat_delete"
    task = db.add_task(chat_id=chat_id, title="Temporary task")

    success = db.delete_task(chat_id=chat_id, task_id=task["id"])
    assert success is True

    # Verify task list is empty
    tasks = db.list_tasks(chat_id=chat_id)
    assert len(tasks) == 0


def test_08_delete_nonexistent_task(setup_temp_db):
    """8. Deleting a nonexistent task returns False."""
    success = db.delete_task(chat_id="chat_123", task_id="nonexistent_id")
    assert success is False


def test_09_chat_isolation(setup_temp_db):
    """9. Verify chat isolation for listing, completing, and deleting tasks."""
    chat_a = "user_chat_A"
    chat_b = "user_chat_B"

    # Chat A creates a task
    task_a = db.add_task(chat_id=chat_a, title="Chat A's private task")

    # Chat B list_tasks must NOT see Chat A's task
    tasks_b = db.list_tasks(chat_id=chat_b)
    assert len(tasks_b) == 0
    assert not any(t["id"] == task_a["id"] for t in tasks_b)

    # Chat B must NOT be able to complete Chat A's task
    complete_result = db.complete_task(chat_id=chat_b, task_id=task_a["id"])
    assert complete_result is None

    # Chat B must NOT be able to delete Chat A's task
    delete_result = db.delete_task(chat_id=chat_b, task_id=task_a["id"])
    assert delete_result is False

    # Verify Chat A's task is still present and not completed
    tasks_a = db.list_tasks(chat_id=chat_a)
    assert len(tasks_a) == 1
    assert tasks_a[0]["id"] == task_a["id"]
    assert tasks_a[0]["done"] == 0
    assert tasks_a[0]["done_at"] is None
