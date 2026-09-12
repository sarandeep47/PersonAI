import pytest
from unittest.mock import patch, MagicMock

import db.session as db
from agent.schemas import ToolCall
from main import execute_single_tool_call, _resolve_task


@pytest.fixture(autouse=True)
def setup_temp_db(tmp_path, monkeypatch):
    """Use a temporary database file for all tests in this module."""
    test_db_path = str(tmp_path / "test_task_exec_session.db")
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_db()
    yield test_db_path


def test_01_add_task_execution(setup_temp_db):
    """1. add_task tool execution creates task and returns formatted response."""
    chat_id = "user_add_exec"
    tc = ToolCall(tool="add_task", args={"title": "Finish my RAG docs"}, reasoning="Test add task")

    success, msg = execute_single_tool_call(tc, chat_id=chat_id)
    assert success is True
    assert "✅ Added task: Finish my RAG docs" in msg

    # Verify task stored in DB
    tasks = db.list_tasks(chat_id)
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Finish my RAG docs"


def test_02_list_tasks_execution(setup_temp_db):
    """2. list_tasks tool execution returns formatted task list."""
    chat_id = "user_list_exec"
    db.add_task(chat_id, "Task A")
    db.add_task(chat_id, "Task B")

    tc = ToolCall(tool="list_tasks", args={}, reasoning="Test list tasks")
    success, msg = execute_single_tool_call(tc, chat_id=chat_id)

    assert success is True
    assert "📝 *Your tasks*" in msg
    assert "Task A" in msg
    assert "Task B" in msg


def test_03_formatted_empty_task_list(setup_temp_db):
    """3. list_tasks on empty database returns formatted empty message."""
    chat_id = "user_empty_exec"
    tc = ToolCall(tool="list_tasks", args={}, reasoning="Test empty list tasks")

    success, msg = execute_single_tool_call(tc, chat_id=chat_id)
    assert success is True
    assert msg == "📝 You don't have any tasks yet."


def test_04_formatted_mixed_completed_incomplete_list(setup_temp_db):
    """4. list_tasks formats incomplete tasks with ⬜ and completed tasks with ✅."""
    chat_id = "user_mixed_exec"
    t1 = db.add_task(chat_id, "Incomplete task")
    t2 = db.add_task(chat_id, "Completed task")
    db.complete_task(chat_id, t2["id"])

    tc = ToolCall(tool="list_tasks", args={}, reasoning="Test mixed list")
    success, msg = execute_single_tool_call(tc, chat_id=chat_id)

    assert success is True
    assert "⬜ Incomplete task" in msg or "⬜" in msg
    assert "✅ Completed task" in msg or "✅" in msg


def test_05_complete_task_by_exact_task_id(setup_temp_db):
    """5. complete_task resolves and marks complete by exact UUID task ID."""
    chat_id = "user_complete_id"
    t = db.add_task(chat_id, "Finish RAG docs")

    tc = ToolCall(tool="complete_task", args={"task_id": t["id"]}, reasoning="Complete by ID")
    success, msg = execute_single_tool_call(tc, chat_id=chat_id)

    assert success is True
    assert "✅ Marked task as complete: Finish RAG docs" in msg

    updated = db.list_tasks(chat_id)[0]
    assert updated["done"] == 1
    assert updated["done_at"] is not None


def test_06_complete_task_by_exact_title(setup_temp_db):
    """6. complete_task resolves and marks complete by exact title string."""
    chat_id = "user_complete_title"
    db.add_task(chat_id, "Finish my RAG docs")

    tc = ToolCall(tool="complete_task", args={"task_id": "Finish my RAG docs"}, reasoning="Complete by title")
    success, msg = execute_single_tool_call(tc, chat_id=chat_id)

    assert success is True
    assert "✅ Marked task as complete: Finish my RAG docs" in msg

    updated = db.list_tasks(chat_id)[0]
    assert updated["done"] == 1


def test_07_complete_task_by_unique_partial_title(setup_temp_db):
    """7. complete_task resolves and marks complete by unique partial title."""
    chat_id = "user_complete_partial"
    db.add_task(chat_id, "Finish my RAG docs")

    tc = ToolCall(tool="complete_task", args={"task_id": "RAG docs"}, reasoning="Complete by partial title")
    success, msg = execute_single_tool_call(tc, chat_id=chat_id)

    assert success is True
    assert "✅ Marked task as complete: Finish my RAG docs" in msg


def test_08_complete_nonexistent_task(setup_temp_db):
    """8. complete_task on nonexistent task returns clear not-found message."""
    chat_id = "user_complete_nonexist"
    tc = ToolCall(tool="complete_task", args={"task_id": "Nonexistent Task"}, reasoning="Nonexistent")

    success, msg = execute_single_tool_call(tc, chat_id=chat_id)
    assert success is False
    assert "❌ I couldn't find a task matching 'Nonexistent Task'" in msg


def test_09_complete_ambiguous_task(setup_temp_db):
    """9. complete_task on ambiguous partial match asks user for clarification."""
    chat_id = "user_complete_ambiguous"
    db.add_task(chat_id, "Finish RAG docs")
    db.add_task(chat_id, "Review RAG docs")

    tc = ToolCall(tool="complete_task", args={"task_id": "RAG docs"}, reasoning="Ambiguous task")
    success, msg = execute_single_tool_call(tc, chat_id=chat_id)

    assert success is False
    assert "Which task did you mean? I found multiple matching tasks:" in msg
    assert "Finish RAG docs" in msg
    assert "Review RAG docs" in msg


def test_10_delete_task_by_exact_task_id(setup_temp_db):
    """10. delete_task resolves and deletes by exact UUID task ID."""
    chat_id = "user_del_id"
    t = db.add_task(chat_id, "Finish my RAG docs")

    tc = ToolCall(tool="delete_task", args={"task_id": t["id"]}, reasoning="Delete by ID")
    success, msg = execute_single_tool_call(tc, chat_id=chat_id)

    assert success is True
    assert "🗑️ Deleted task: Finish my RAG docs" in msg
    assert len(db.list_tasks(chat_id)) == 0


def test_11_delete_task_by_exact_title(setup_temp_db):
    """11. delete_task resolves and deletes by exact title."""
    chat_id = "user_del_title"
    db.add_task(chat_id, "Finish my RAG docs")

    tc = ToolCall(tool="delete_task", args={"task_id": "Finish my RAG docs"}, reasoning="Delete by title")
    success, msg = execute_single_tool_call(tc, chat_id=chat_id)

    assert success is True
    assert "🗑️ Deleted task: Finish my RAG docs" in msg
    assert len(db.list_tasks(chat_id)) == 0


def test_12_delete_task_by_unique_partial_title(setup_temp_db):
    """12. delete_task resolves and deletes by unique partial title."""
    chat_id = "user_del_partial"
    db.add_task(chat_id, "Finish my RAG docs")

    tc = ToolCall(tool="delete_task", args={"task_id": "RAG docs"}, reasoning="Delete by partial")
    success, msg = execute_single_tool_call(tc, chat_id=chat_id)

    assert success is True
    assert "🗑️ Deleted task: Finish my RAG docs" in msg
    assert len(db.list_tasks(chat_id)) == 0


def test_13_delete_nonexistent_task(setup_temp_db):
    """13. delete_task on nonexistent task returns clear not-found message."""
    chat_id = "user_del_nonexist"
    tc = ToolCall(tool="delete_task", args={"task_id": "Nonexistent Task"}, reasoning="Nonexistent")

    success, msg = execute_single_tool_call(tc, chat_id=chat_id)
    assert success is False
    assert "❌ I couldn't find a task matching 'Nonexistent Task'" in msg


def test_14_delete_ambiguous_task(setup_temp_db):
    """14. delete_task on ambiguous partial match asks user for clarification."""
    chat_id = "user_del_ambiguous"
    db.add_task(chat_id, "Finish RAG docs")
    db.add_task(chat_id, "Review RAG docs")

    tc = ToolCall(tool="delete_task", args={"task_id": "RAG docs"}, reasoning="Ambiguous delete")
    success, msg = execute_single_tool_call(tc, chat_id=chat_id)

    assert success is False
    assert "Which task did you mean? I found multiple matching tasks:" in msg
    assert "Finish RAG docs" in msg
    assert "Review RAG docs" in msg


def test_15_chat_id_isolation_for_complete(setup_temp_db):
    """15. Chat B cannot complete Chat A's task by ID or title."""
    chat_a = "user_iso_complete_A"
    chat_b = "user_iso_complete_B"

    task_a = db.add_task(chat_a, "Secret task for A")

    # Chat B trying to complete by ID
    tc_id = ToolCall(tool="complete_task", args={"task_id": task_a["id"]}, reasoning="Chat B complete attempt")
    success_id, msg_id = execute_single_tool_call(tc_id, chat_id=chat_b)
    assert success_id is False
    assert "❌ I couldn't find a task matching" in msg_id

    # Chat B trying to complete by title
    tc_title = ToolCall(tool="complete_task", args={"task_id": "Secret task for A"}, reasoning="Chat B title attempt")
    success_title, msg_title = execute_single_tool_call(tc_title, chat_id=chat_b)
    assert success_title is False
    assert "❌ I couldn't find a task matching" in msg_title

    # Verify Chat A's task remains uncompleted
    assert db.list_tasks(chat_a)[0]["done"] == 0


def test_16_chat_id_isolation_for_delete(setup_temp_db):
    """16. Chat B cannot delete Chat A's task by ID or title."""
    chat_a = "user_iso_del_A"
    chat_b = "user_iso_del_B"

    task_a = db.add_task(chat_a, "Secret task for A")

    # Chat B trying to delete by ID
    tc_id = ToolCall(tool="delete_task", args={"task_id": task_a["id"]}, reasoning="Chat B delete attempt")
    success_id, msg_id = execute_single_tool_call(tc_id, chat_id=chat_b)
    assert success_id is False
    assert "❌ I couldn't find a task matching" in msg_id

    # Chat B trying to delete by title
    tc_title = ToolCall(tool="delete_task", args={"task_id": "Secret task for A"}, reasoning="Chat B delete title attempt")
    success_title, msg_title = execute_single_tool_call(tc_title, chat_id=chat_b)
    assert success_title is False
    assert "❌ I couldn't find a task matching" in msg_title

    # Verify Chat A's task remains in DB
    assert len(db.list_tasks(chat_a)) == 1


def test_17_database_exception_handling(setup_temp_db, monkeypatch):
    """17. Database failure returns sanitized user-facing error message without crashing."""
    chat_id = "user_db_error"

    def mock_db_error(*args, **kwargs):
        raise RuntimeError("sqlite3.OperationalError: database locked / sqlite trace error secret_key=123")

    monkeypatch.setattr(db, "add_task", mock_db_error)

    tc = ToolCall(tool="add_task", args={"title": "Test crash"}, reasoning="Test error handling")
    success, msg = execute_single_tool_call(tc, chat_id=chat_id)

    assert success is False
    assert "Execution error:" in msg
    assert "secret_key" not in msg  # sanitized


def test_18_existing_tools_unaffected(setup_temp_db):
    """18. Existing tools (e.g. none) continue to execute properly."""
    chat_id = "user_unaffected"
    tc = ToolCall(tool="none", args={"message": "Hello user!"}, reasoning="Greeting")

    success, msg = execute_single_tool_call(tc, chat_id=chat_id)
    assert success is True
    assert msg == "Hello user!"
