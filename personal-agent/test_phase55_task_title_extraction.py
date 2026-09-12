import pytest
import db.session as db
from agent.schemas import ToolCall
from agent.core import clean_task_title, validate_tool_call
from main import execute_single_tool_call


@pytest.fixture(autouse=True)
def setup_temp_db(tmp_path, monkeypatch):
    """Use a temporary database file for all tests in this module."""
    test_db_path = str(tmp_path / "test_task_title_session.db")
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_db()
    yield test_db_path


def test_01_exact_bug_regression():
    """1. Exact bug regression: 'add Test Telegram integration to my task' extracts 'Test Telegram integration'."""
    raw_title = "add Test Telegram integration to my task"
    user_msg = "add Test Telegram integration to my task"

    cleaned = clean_task_title(raw_title, user_msg)
    assert cleaned == "Test Telegram integration"

    tc = ToolCall(tool="add_task", args={"title": raw_title}, reasoning="Regression test")
    validated = validate_tool_call(tc, user_msg)
    assert validated.tool == "add_task"
    assert validated.args["title"] == "Test Telegram integration"


def test_02_variation_add_buy_groceries():
    """2. Variation: 'Add buy groceries to my tasks' -> 'buy groceries'."""
    user_msg = "Add buy groceries to my tasks"
    cleaned = clean_task_title("Add buy groceries to my tasks", user_msg)
    assert cleaned == "buy groceries"

    tc = ToolCall(tool="add_task", args={"title": "Add buy groceries to my tasks"}, reasoning="Test variation")
    validated = validate_tool_call(tc, user_msg)
    assert validated.args["title"] == "buy groceries"


def test_03_variation_add_finish_rag_docs():
    """3. Variation: 'Add finish my RAG docs to my tasks' -> 'finish my RAG docs'."""
    user_msg = "Add finish my RAG docs to my tasks"
    cleaned = clean_task_title("Add finish my RAG docs to my tasks", user_msg)
    assert cleaned == "finish my RAG docs"

    tc = ToolCall(tool="add_task", args={"title": "Add finish my RAG docs to my tasks"}, reasoning="Test variation")
    validated = validate_tool_call(tc, user_msg)
    assert validated.args["title"] == "finish my RAG docs"


def test_04_variation_create_task_to_test():
    """4. Variation: 'Create a task to test Telegram integration' -> 'test Telegram integration'."""
    user_msg = "Create a task to test Telegram integration"
    cleaned = clean_task_title("Create a task to test Telegram integration", user_msg)
    assert cleaned == "test Telegram integration"

    tc = ToolCall(tool="add_task", args={"title": "Create a task to test Telegram integration"}, reasoning="Test variation")
    validated = validate_tool_call(tc, user_msg)
    assert validated.args["title"] == "test Telegram integration"


def test_05_variation_add_a_task_colon():
    """5. Variation: 'Add a task: test Telegram integration' -> 'test Telegram integration'."""
    user_msg = "Add a task: test Telegram integration"
    cleaned = clean_task_title("Add a task: test Telegram integration", user_msg)
    assert cleaned == "test Telegram integration"

    tc = ToolCall(tool="add_task", args={"title": "Add a task: test Telegram integration"}, reasoning="Test variation")
    validated = validate_tool_call(tc, user_msg)
    assert validated.args["title"] == "test Telegram integration"


def test_06_preserve_legitimate_add_title():
    """6. Preserve legitimate task titles starting with 'Add' when explicitly named/quoted."""
    user_msg = "Add a task called 'Add Telegram support'"
    cleaned = clean_task_title("Add Telegram support", user_msg)
    assert cleaned == "Add Telegram support"

    tc = ToolCall(tool="add_task", args={"title": "'Add Telegram support'"}, reasoning="Test preserve Add")
    validated = validate_tool_call(tc, user_msg)
    assert validated.args["title"] == "Add Telegram support"


def test_07_end_to_end_add_task_execution_title(setup_temp_db):
    """7. End-to-end execution of add_task tool returns cleaned task title in confirmation."""
    chat_id = "user_e2e_title"
    user_msg = "add Test Telegram integration to my task"

    raw_tc = ToolCall(tool="add_task", args={"title": user_msg}, reasoning="E2E test")
    validated_tc = validate_tool_call(raw_tc, user_msg)

    success, msg = execute_single_tool_call(validated_tc, chat_id=chat_id)
    assert success is True
    assert msg == "✅ Added task: Test Telegram integration"

    tasks = db.list_tasks(chat_id)
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Test Telegram integration"
