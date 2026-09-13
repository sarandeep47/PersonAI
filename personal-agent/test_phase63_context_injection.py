# test_phase63_context_injection.py
import pytest
import json
from unittest.mock import patch, MagicMock
import db.session as db
from agent.core import format_recent_context, call_agent
from agent.prompts import AGENT_SYSTEM_PROMPT

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Isolated temporary database for Phase 6.3 Context Injection testing."""
    db_file = str(tmp_path / "test_agent_session.db")
    monkeypatch.setattr(db, "DB_PATH", db_file)
    db.init_db()
    yield db_file


def test_01_calendar_context_injected():
    """Verify calendar context entity is formatted and injected into recent context block."""
    chat_id = "chat_inj_cal"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="evt_101",
        title="Sprint Planning",
        details="Friday 10:00 AM"
    )

    formatted = format_recent_context(chat_id)
    assert "--- RECENT CONTEXT ---" in formatted
    assert "--- END RECENT CONTEXT ---" in formatted
    assert "Calendar: Sprint Planning — Friday 10:00 AM (ID: evt_101)" in formatted


def test_02_task_context_injected():
    """Verify task context entity is formatted and injected into recent context block."""
    chat_id = "chat_inj_task"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="task",
        entity_id="task_202",
        title="Finish my RAG docs",
        details="Status: pending"
    )

    formatted = format_recent_context(chat_id)
    assert "--- RECENT CONTEXT ---" in formatted
    assert "Task: Finish my RAG docs — Status: pending (ID: task_202)" in formatted


def test_03_email_context_injected():
    """Verify email context entity is formatted and injected without dumping full body."""
    chat_id = "chat_inj_email"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="email",
        entity_id="draft_303",
        title="Draft to messy7@gmail.com",
        details=json.dumps({"to": "messy7@gmail.com", "subject": "Notification of Termination", "body": "Very long body text..."})
    )

    formatted = format_recent_context(chat_id)
    assert "--- RECENT CONTEXT ---" in formatted
    assert "Email: Draft to messy7@gmail.com — To: messy7@gmail.com, Subject: Notification of Termination (ID: draft_303)" in formatted
    # Ensure full raw body string is not dumped unnecessarily
    assert "Very long body text" not in formatted


def test_04_multiple_context_types_injected_together():
    """Verify calendar, task, and email contexts are all injected in the same block when present."""
    chat_id = "chat_inj_multi"
    db.save_context_entity(chat_id, "calendar", "evt_1", "Meeting with Priya", "Friday 3:00 PM")
    db.save_context_entity(chat_id, "task", "task_1", "Finish my RAG docs", "Status: pending")
    db.save_context_entity(chat_id, "email", "draft_1", "Draft to messy7@gmail.com", json.dumps({"to": "messy7@gmail.com", "subject": "Termination"}))

    formatted = format_recent_context(chat_id)
    assert "--- RECENT CONTEXT ---" in formatted
    assert "Calendar: Meeting with Priya" in formatted
    assert "Task: Finish my RAG docs" in formatted
    assert "Email: Draft to messy7@gmail.com" in formatted
    assert "--- END RECENT CONTEXT ---" in formatted


def test_05_missing_context_handled_cleanly():
    """Verify missing context returns empty string cleanly."""
    assert format_recent_context("non_existent_chat") == ""
    assert format_recent_context(None) == ""


def test_06_context_scoped_to_chat_id():
    """Verify context in chat_A does not leak into chat_B."""
    db.save_context_entity("chat_A", "task", "task_A", "Secret Task A", "pending")

    formatted_a = format_recent_context("chat_A")
    formatted_b = format_recent_context("chat_B")

    assert "Secret Task A" in formatted_a
    assert formatted_b == ""


def test_07_context_injected_at_correct_point_in_llm_call():
    """Verify call_agent appends recent context to system prompt passed to _ollama_chat."""
    chat_id = "chat_inj_agent"
    db.save_context_entity(chat_id, "calendar", "evt_7", "Project Review", "Tomorrow 2 PM")

    dummy_json_res = '{"tool": "none", "args": {"message": "Hello"}, "reasoning": "General chat."}'

    with patch("agent.core._ollama_chat", return_value=dummy_json_res) as mock_ollama:
        call_agent("Hello", chat_id=chat_id)
        
        assert mock_ollama.call_count == 1
        system_prompt_used = mock_ollama.call_args[0][1]
        
        # Verify existing system prompt is retained
        assert AGENT_SYSTEM_PROMPT in system_prompt_used
        # Verify RECENT CONTEXT block appears in the system prompt
        assert "--- RECENT CONTEXT ---" in system_prompt_used
        assert "Calendar: Project Review" in system_prompt_used
        assert "--- END RECENT CONTEXT ---" in system_prompt_used


def test_08_existing_system_prompt_retained():
    """Verify that existing system prompt instructions and tool definitions are preserved."""
    chat_id = "chat_inj_retained"
    db.save_context_entity(chat_id, "task", "t_1", "Test Task", "pending")

    dummy_json_res = '{"tool": "none", "args": {"message": "OK"}, "reasoning": "No action needed."}'

    with patch("agent.core._ollama_chat", return_value=dummy_json_res) as mock_ollama:
        call_agent("Check my status", chat_id=chat_id)
        system_prompt = mock_ollama.call_args[0][1]

        assert "You are a personal AI email assistant" in system_prompt
        assert "1. send_email" in system_prompt
        assert "11. add_task" in system_prompt
        assert "13. complete_task" in system_prompt


def test_09_full_conversation_history_not_injected_in_context_block():
    """Verify full conversation history items are passed in messages, not in RECENT CONTEXT block."""
    chat_id = "chat_inj_history"
    history = [
        {"role": "user", "content": "Previous message 1"},
        {"role": "assistant", "content": "Previous response 1"}
    ]

    dummy_json_res = '{"tool": "none", "args": {"message": "Hi"}, "reasoning": "Greeting."}'

    with patch("agent.core._ollama_chat", return_value=dummy_json_res) as mock_ollama:
        call_agent("Hello again", history=history, chat_id=chat_id)
        
        messages_passed = mock_ollama.call_args[0][0]
        system_prompt = mock_ollama.call_args[0][1]

        # History should be in messages array, NOT in RECENT CONTEXT block
        assert len(messages_passed) == 3
        assert messages_passed[0]["content"] == "Previous message 1"
        assert "Previous message 1" not in system_prompt


def test_10_unrelated_general_query_behavior_unaffected():
    """Verify general conversation flow behaves normally with or without context injection."""
    chat_id = "chat_inj_general"
    dummy_json_res = '{"tool": "none", "args": {"message": "I am doing great!"}, "reasoning": "Greeting response."}'

    with patch("agent.core._ollama_chat", return_value=dummy_json_res):
        res = call_agent("How are you today?", chat_id=chat_id)
        assert res.tool == "none"
        assert res.args["message"] == "I am doing great!"
