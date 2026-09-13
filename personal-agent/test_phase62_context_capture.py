# test_phase62_context_capture.py
import pytest
import os
import json
import time
from unittest.mock import patch, MagicMock
import db.session as db
from main import execute_single_tool_call, _present_email_confirmation
from agent.schemas import ToolCall

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Isolated temporary database for Phase 6.2 Context Capture testing."""
    db_file = str(tmp_path / "test_agent_session.db")
    monkeypatch.setattr(db, "DB_PATH", db_file)
    db.init_db()
    yield db_file


# --- TASK CONTEXT TESTS ---

def test_01_successful_task_creation_stores_context():
    """Verify add_task stores task context entity on success."""
    chat_id = "chat_task_01"
    tool_call = ToolCall(tool="add_task", args={"title": "finish my RAG docs"})

    success, msg = execute_single_tool_call(tool_call, chat_id=chat_id)
    assert success is True

    ctx = db.get_context_entity(chat_id, "task")
    assert ctx is not None
    assert ctx["chat_id"] == chat_id
    assert ctx["entity_type"] == "task"
    assert ctx["title"] == "finish my RAG docs"
    assert ctx["details"] == "Status: pending"
    assert len(ctx["entity_id"]) > 0


def test_02_task_reference_updates_context():
    """Verify complete_task and delete_task update task context to the referenced task."""
    chat_id = "chat_task_02"
    t1 = db.add_task(chat_id, "first task")
    t2 = db.add_task(chat_id, "second task")

    # Complete second task
    tool_call_comp = ToolCall(tool="complete_task", args={"task_id": "second task"})
    success, msg = execute_single_tool_call(tool_call_comp, chat_id=chat_id)
    assert success is True

    ctx_comp = db.get_context_entity(chat_id, "task")
    assert ctx_comp is not None
    assert ctx_comp["entity_id"] == t2["id"]
    assert ctx_comp["title"] == "second task"
    assert ctx_comp["details"] == "Status: completed"

    # Delete first task
    tool_call_del = ToolCall(tool="delete_task", args={"task_id": "first task"})
    success, msg = execute_single_tool_call(tool_call_del, chat_id=chat_id)
    assert success is True

    ctx_del = db.get_context_entity(chat_id, "task")
    assert ctx_del is not None
    assert ctx_del["entity_id"] == t1["id"]
    assert ctx_del["title"] == "first task"
    assert ctx_del["details"] == "Status: deleted"


def test_03_failed_task_operation_does_not_store_false_context():
    """Verify failed/invalid task operations do NOT save or alter context."""
    chat_id = "chat_task_03"
    
    # Empty title
    tool_call_empty = ToolCall(tool="add_task", args={"title": ""})
    success, msg = execute_single_tool_call(tool_call_empty, chat_id=chat_id)
    assert success is False
    assert db.get_context_entity(chat_id, "task") is None

    # Nonexistent task completion
    tool_call_nonexistent = ToolCall(tool="complete_task", args={"task_id": "nonexistent task title"})
    success, msg = execute_single_tool_call(tool_call_nonexistent, chat_id=chat_id)
    assert success is False
    assert db.get_context_entity(chat_id, "task") is None


def test_04_task_chat_isolation():
    """Verify task context captured in chat_A does not leak into chat_B."""
    execute_single_tool_call(ToolCall(tool="add_task", args={"title": "User A Task"}), chat_id="chat_A")

    ctx_a = db.get_context_entity("chat_A", "task")
    ctx_b = db.get_context_entity("chat_B", "task")

    assert ctx_a is not None
    assert ctx_a["title"] == "User A Task"
    assert ctx_b is None


# --- CALENDAR CONTEXT TESTS ---

def test_05_successful_calendar_creation_stores_context():
    """Verify schedule_calendar stores calendar context entity on success."""
    chat_id = "chat_cal_01"
    mock_res = {
        "status": "success",
        "id": "google_event_999",
        "title": "Meeting with Priya",
        "start": "2026-09-15T15:00:00",
        "end": "2026-09-15T15:30:00",
    }

    with patch("main.create_event", return_value=mock_res):
        tc = ToolCall(tool="schedule_calendar", args={
            "title": "Meeting with Priya",
            "date": "2026-09-15",
            "start_time": "15:00",
            "duration_minutes": 30
        })
        success, msg = execute_single_tool_call(tc, chat_id=chat_id)
        assert success is True

    ctx = db.get_context_entity(chat_id, "calendar")
    assert ctx is not None
    assert ctx["chat_id"] == chat_id
    assert ctx["entity_type"] == "calendar"
    assert ctx["entity_id"] == "google_event_999"
    assert ctx["title"] == "Meeting with Priya"
    assert "2026-09-15T15:00:00" in ctx["details"]


def test_06_failed_calendar_operation_does_not_store_context():
    """Verify failed calendar creation does NOT store context."""
    chat_id = "chat_cal_02"
    mock_err = {"status": "error", "message": "API network failure"}

    with patch("main.create_event", return_value=mock_err):
        tc = ToolCall(tool="schedule_calendar", args={
            "title": "Broken Meeting",
            "date": "2026-09-15",
            "start_time": "15:00"
        })
        success, msg = execute_single_tool_call(tc, chat_id=chat_id)
        assert success is False

    assert db.get_context_entity(chat_id, "calendar") is None


# --- EMAIL CONTEXT TESTS ---

def test_07_successful_email_draft_and_send_stores_context():
    """Verify email draft and sending capture email context using valid existing action/recipient IDs."""
    chat_id = "chat_email_01"
    
    # 1. Draft Email via _present_email_confirmation
    with patch("main.send_telegram_message"):
        _present_email_confirmation(chat_id, {
            "to": "priya@example.com",
            "subject": "Project Update",
            "body": "Here is the weekly report."
        })

    ctx_draft = db.get_context_entity(chat_id, "email")
    assert ctx_draft is not None
    assert ctx_draft["chat_id"] == chat_id
    assert ctx_draft["entity_type"] == "email"
    assert ctx_draft["entity_id"].startswith("draft_")
    assert "priya@example.com" in ctx_draft["title"]

    # 2. Direct Send Email via send_email tool
    with patch("main.send_email_raw", return_value=True):
        tc_send = ToolCall(tool="send_email", args={
            "to": "bob@example.com",
            "subject": "Direct Email",
            "body": "Hello Bob"
        })
        success, msg = execute_single_tool_call(tc_send, chat_id=chat_id)
        assert success is True

    ctx_send = db.get_context_entity(chat_id, "email")
    assert ctx_send is not None
    assert ctx_send["entity_id"] == "sent_bob@example.com"
    assert "bob@example.com" in ctx_send["title"]


def test_08_failed_email_operation_does_not_store_context():
    """Verify invalid email recipient/failed send does NOT store false email context."""
    chat_id = "chat_email_02"

    tc_invalid = ToolCall(tool="send_email", args={
        "to": "invalid_email_format",
        "subject": "Test",
        "body": "Test body"
    })
    success, msg = execute_single_tool_call(tc_invalid, chat_id=chat_id)
    assert success is False
    assert db.get_context_entity(chat_id, "email") is None


def test_09_read_and_reply_email_captures_context():
    """Verify read_email and draft_reply capture email context."""
    chat_id = "chat_email_03"

    tc_read = ToolCall(tool="read_email", args={"email_id": "msg_abc123"})
    success, msg = execute_single_tool_call(tc_read, chat_id=chat_id)
    assert success is True

    ctx_read = db.get_context_entity(chat_id, "email")
    assert ctx_read is not None
    assert ctx_read["entity_id"] == "msg_abc123"

    tc_reply = ToolCall(tool="draft_reply", args={"email_id": "msg_xyz789", "instructions": "Sounds good"})
    success, msg = execute_single_tool_call(tc_reply, chat_id=chat_id)
    assert success is True

    ctx_reply = db.get_context_entity(chat_id, "email")
    assert ctx_reply is not None
    assert ctx_reply["entity_id"] == "msg_xyz789"


# --- ENTITY INDEPENDENCE TESTS ---

def test_10_entity_types_remain_independent():
    """Verify task, calendar, and email contexts do not overwrite each other."""
    chat_id = "chat_multi_entity"

    # Save task
    execute_single_tool_call(ToolCall(tool="add_task", args={"title": "Buy milk"}), chat_id=chat_id)
    
    # Save calendar
    mock_res = {"status": "success", "id": "cal_11", "title": "Doctor Appt", "start": "2026-09-20T10:00:00"}
    with patch("main.create_event", return_value=mock_res):
        execute_single_tool_call(ToolCall(tool="schedule_calendar", args={"title": "Doctor Appt", "date": "2026-09-20", "start_time": "10:00"}), chat_id=chat_id)

    # Save email
    with patch("main.send_email_raw", return_value=True):
        execute_single_tool_call(ToolCall(tool="send_email", args={"to": "doc@example.com", "subject": "Appt Confirmation", "body": "Confirmed"}), chat_id=chat_id)

    ctx_task = db.get_context_entity(chat_id, "task")
    ctx_cal = db.get_context_entity(chat_id, "calendar")
    ctx_email = db.get_context_entity(chat_id, "email")

    assert ctx_task["title"] == "Buy milk"
    assert ctx_cal["title"] == "Doctor Appt"
    assert ctx_email["entity_id"] == "sent_doc@example.com"

    # Update task — calendar and email must remain unchanged
    execute_single_tool_call(ToolCall(tool="add_task", args={"title": "New Task Title"}), chat_id=chat_id)

    assert db.get_context_entity(chat_id, "task")["title"] == "New Task Title"
    assert db.get_context_entity(chat_id, "calendar")["title"] == "Doctor Appt"
    assert db.get_context_entity(chat_id, "email")["entity_id"] == "sent_doc@example.com"
