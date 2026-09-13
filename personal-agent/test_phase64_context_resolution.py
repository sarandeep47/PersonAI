# test_phase64_context_resolution.py
import pytest
import json
import time
from unittest.mock import patch, MagicMock
import db.session as db
from main import execute_single_tool_call, _resolve_task, handle_callback_query
from agent.core import validate_tool_call, call_agent
from agent.schemas import ToolCall

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Isolated temporary database for Phase 6.4 Context Resolution testing."""
    db_file = str(tmp_path / "test_agent_session.db")
    monkeypatch.setattr(db, "DB_PATH", db_file)
    db.init_db()
    yield db_file


# --- END-TO-END SEQUENTIAL TESTS ---

def test_01_e2e_task_creation_and_completion_via_pronoun():
    """
    Sequence 1:
    1. "Add Test Telegram integration to my tasks." -> task created
    2. "Mark that as done." -> completed task created in step 1
    """
    chat_id = "chat_e2e_task"

    # Step 1: Create task
    tc_add = ToolCall(tool="add_task", args={"title": "Test Telegram integration"})
    success, msg = execute_single_tool_call(tc_add, chat_id=chat_id)
    assert success is True
    assert "Test Telegram integration" in msg

    # Verify task context is stored
    ctx = db.get_context_entity(chat_id, "task")
    assert ctx is not None
    assert ctx["title"] == "Test Telegram integration"

    # Step 2: Complete task via pronoun reference "that"
    tc_comp = ToolCall(tool="complete_task", args={"task_id": "that"})
    validated_tc = validate_tool_call(tc_comp, "Mark that as done.", chat_id=chat_id)
    assert validated_tc.tool == "complete_task"

    success_comp, msg_comp = execute_single_tool_call(validated_tc, chat_id=chat_id)
    assert success_comp is True
    assert "Marked task as complete: Test Telegram integration" in msg_comp

    # Verify database task status is done
    tasks = db.list_tasks(chat_id)
    assert len(tasks) == 1
    assert tasks[0]["done"] == 1


def test_02_e2e_calendar_creation_and_modification_via_pronoun():
    """
    Sequence 2:
    1. Create a calendar event ("Meeting with Priya", 3 PM).
    2. "Make it 4 PM instead." -> updates same event date/title.
    """
    chat_id = "chat_e2e_cal"
    mock_event_1 = {
        "status": "success",
        "id": "evt_priya_100",
        "title": "Meeting with Priya",
        "start": "2026-09-18T15:00:00",
        "end": "2026-09-18T15:30:00"
    }

    # Step 1: Schedule calendar event
    with patch("main.create_event", return_value=mock_event_1):
        tc_sched = ToolCall(tool="schedule_calendar", args={
            "title": "Meeting with Priya",
            "date": "2026-09-18",
            "start_time": "15:00",
            "duration_minutes": 30
        })
        success, msg = execute_single_tool_call(tc_sched, chat_id=chat_id)
        assert success is True

    ctx_cal = db.get_context_entity(chat_id, "calendar")
    assert ctx_cal is not None
    assert ctx_cal["title"] == "Meeting with Priya"
    assert ctx_cal["entity_id"] == "evt_priya_100"

    # Step 2: "Make it 4 PM instead" -> validate_tool_call resolves "it" to "Meeting with Priya" & date "2026-09-18"
    tc_update = ToolCall(tool="schedule_calendar", args={"title": "it", "start_time": "16:00"})
    validated_tc = validate_tool_call(tc_update, "Make it 4 PM instead.", chat_id=chat_id)
    assert validated_tc.tool == "schedule_calendar"
    assert validated_tc.args["title"] == "Meeting with Priya"
    assert validated_tc.args["date"] == "2026-09-18"
    assert validated_tc.args["start_time"] == "16:00"


# --- TASK REFERENCE RESOLUTION TESTS ---

def test_03_task_that_task_resolves_to_stored_task():
    """Verify 'that task' resolves to stored task when multiple tasks exist in DB."""
    chat_id = "chat_ref_task"
    t1 = db.add_task(chat_id, "Old task")
    t2 = db.add_task(chat_id, "Recent RAG docs")

    # Update context entity to point to t2
    db.save_context_entity(chat_id, "task", t2["id"], t2["title"], "Status: pending")

    resolved_task, candidates, status = _resolve_task(chat_id, "that task")
    assert status == "UNIQUE"
    assert resolved_task["id"] == t2["id"]
    assert resolved_task["title"] == "Recent RAG docs"


def test_04_no_task_context_returns_clarification_for_multiple_tasks():
    """Verify multiple tasks with no stored context requires clarification instead of guessing."""
    chat_id = "chat_no_task_ctx"
    db.add_task(chat_id, "Task 1")
    db.add_task(chat_id, "Task 2")

    resolved_task, candidates, status = _resolve_task(chat_id, "that task")
    assert status == "MULTIPLE"
    assert len(candidates) == 2


def test_05_stale_deleted_task_context_handled_safely():
    """Verify stored context pointing to a deleted task does NOT resolve to deleted task."""
    chat_id = "chat_stale_task"
    t1 = db.add_task(chat_id, "Active Task 1")
    t2 = db.add_task(chat_id, "Active Task 2")

    # Save task context as deleted
    db.save_context_entity(chat_id, "task", "deleted_uuid_999", "Old Deleted Task", "Status: deleted")

    resolved_task, candidates, status = _resolve_task(chat_id, "that task")
    # Should ignore deleted context and ask for clarification among active tasks
    assert status == "MULTIPLE"
    assert len(candidates) == 2


# --- CALENDAR REFERENCE RESOLUTION TESTS ---

def test_06_calendar_generic_references_resolve_to_stored_context():
    """Verify 'the meeting', 'that meeting', and 'it' resolve to stored calendar context."""
    chat_id = "chat_ref_cal"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="google_evt_456",
        title="Weekly Sync",
        details="2026-09-25T14:00:00"
    )

    for ref in ["the meeting", "that meeting", "it", "this event"]:
        tc = ToolCall(tool="schedule_calendar", args={"title": ref, "start_time": "15:00"})
        val_tc = validate_tool_call(tc, f"Reschedule {ref} to 3 PM", chat_id=chat_id)
        assert val_tc.tool == "schedule_calendar"
        assert val_tc.args["title"] == "Weekly Sync"
        assert val_tc.args["date"] == "2026-09-25"


def test_07_no_calendar_context_returns_safe_clarification():
    """Verify generic calendar reference with no stored calendar context returns tool 'none' clarification."""
    chat_id = "chat_no_cal_ctx"
    tc = ToolCall(tool="schedule_calendar", args={"title": "it", "start_time": "16:00"})

    val_tc = validate_tool_call(tc, "Make it 4 PM instead.", chat_id=chat_id)
    assert val_tc.tool == "none"
    assert "couldn't find a recent calendar meeting" in val_tc.args["message"].lower()


# --- EMAIL REFERENCE RESOLUTION TESTS ---

def test_08_email_context_resolution():
    """Verify 'that email' and 'the email' resolve using stored email context."""
    chat_id = "chat_ref_email"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="email",
        entity_id="draft_abc123",
        title="Draft to test@example.com",
        details=json.dumps({"to": "test@example.com", "subject": "Initial Subject", "body": "Initial Body"})
    )

    ctx = db.get_context_entity(chat_id, "email")
    assert ctx is not None
    assert ctx["entity_id"] == "draft_abc123"
    assert "test@example.com" in ctx["details"]


# --- GENERAL & EXPLICIT REFERENCE TESTS ---

def test_09_explicit_references_unaffected_by_context():
    """Verify explicit title references continue to work without interference from context."""
    chat_id = "chat_explicit_refs"
    t1 = db.add_task(chat_id, "finish my RAG docs")
    t2 = db.add_task(chat_id, "buy groceries")

    # Store t1 in context
    db.save_context_entity(chat_id, "task", t1["id"], t1["title"], "Status: pending")

    # Explicit reference to t2
    resolved_task, candidates, status = _resolve_task(chat_id, "buy groceries")
    assert status == "EXACT"
    assert resolved_task["id"] == t2["id"]


def test_10_chat_isolation_of_context_resolution():
    """Verify context in chat_A does not resolve references for chat_B."""
    db.add_task("chat_A", "User A Task")
    db.save_context_entity("chat_A", "task", "id_a", "User A Task", "Status: pending")

    db.add_task("chat_B", "User B Task 1")
    db.add_task("chat_B", "User B Task 2")

    # In chat_B, "that task" should NOT resolve User A's task
    resolved_task, candidates, status = _resolve_task("chat_B", "that task")
    assert status == "MULTIPLE"
    assert len(candidates) == 2


def test_11_calendar_confirmation_persists_real_event_id():
    """
    Verify Fix 6.4a:
    1. Calendar confirmation initially has a temporary cal_ action ID.
    2. create_event() returns a real Google Calendar event ID.
    3. confirm_cal saves the real event ID into context_entities.
    4. A subsequent context lookup returns the real Google Calendar ID.
    5. Existing title/date/time details remain intact.
    """
    chat_id = "chat_fix_64a"
    temp_action_id = "cal_temp_action_123"

    # Step 1: Simulate initial presentation (saves temporary cal_ action ID)
    db.save_pending_action(temp_action_id, chat_id, "confirm_schedule_calendar", {
        "title": "tomorrow for teps",
        "date": "2026-09-13",
        "start_time": "18:00",
        "duration_minutes": 60,
        "attendees": None,
        "event_id": None,
    })
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id=temp_action_id,
        title="tomorrow for teps",
        details="2026-09-13 18:00"
    )

    initial_ctx = db.get_context_entity(chat_id, "calendar")
    assert initial_ctx is not None
    assert initial_ctx["entity_id"] == "cal_temp_action_123"
    assert initial_ctx["title"] == "tomorrow for teps"

    # Step 2 & 3: Trigger confirm_cal with mocked create_event returning real Google Calendar ID
    real_gcal_id = "gcal_evt_real_999"
    mock_res = {
        "status": "success",
        "id": real_gcal_id,
        "title": "tomorrow for teps",
        "start": "2026-09-13T18:00:00",
        "end": "2026-09-13T19:00:00",
        "htmlLink": "https://calendar.google.com/event?id=real_999"
    }

    cq = {
        "id": "cq_100",
        "data": f"confirm_cal:{temp_action_id}",
        "message": {"chat": {"id": chat_id}}
    }

    with patch("main.create_event", return_value=mock_res), \
         patch("main.send_telegram_message"), \
         patch("main.answer_callback_query"):
        handle_callback_query(cq)

    # Step 4 & 5: Subsequent context lookup returns real ID and preserved title/date/time details
    updated_ctx = db.get_context_entity(chat_id, "calendar")
    assert updated_ctx is not None
    assert updated_ctx["entity_id"] == real_gcal_id
    assert updated_ctx["title"] == "tomorrow for teps"
    assert "2026-09-13" in updated_ctx["details"]
    assert "18:00:00" in updated_ctx["details"]


# --- FIX 6.4b FOCUSED DATE PRESERVATION TESTS ---

def test_12_reschedule_time_only_preserves_tomorrow_date():
    """
    Verify Fix 6.4b:
    Existing event set for tomorrow (2026-09-13) + time-only "make it 7 PM" -> preserves tomorrow's date (2026-09-13).
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64b_tomorrow"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_tomorrow",
        title="tomorrow for teps",
        details="2026-09-13 18:00"
    )

    ref_dt = datetime(2026, 9, 12, 12, 0, 0).astimezone() # Today is Saturday Sept 12; Event is Sunday Sept 13
    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "make it 7 PM", chat_id=chat_id, reference_datetime=ref_dt)
    assert res.tool == "schedule_calendar"
    assert res.args["date"] == "2026-09-13"
    assert res.args["start_time"] == "19:00"
    assert res.args["event_id"] == "gcal_evt_tomorrow"


def test_13_reschedule_time_only_preserves_today_date():
    """
    Verify Fix 6.4b:
    Existing event set for today (2026-09-12) + time-only "make it 7 PM" -> preserves today's date (2026-09-12).
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64b_today"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_today",
        title="team sync",
        details="2026-09-12 18:00"
    )

    ref_dt = datetime(2026, 9, 12, 12, 0, 0).astimezone() # Today is Saturday Sept 12; Event is Saturday Sept 12
    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "make it 7 PM", chat_id=chat_id, reference_datetime=ref_dt)
    assert res.tool == "schedule_calendar"
    assert res.args["date"] == "2026-09-12"
    assert res.args["start_time"] == "19:00"
    assert res.args["event_id"] == "gcal_evt_today"


def test_14_reschedule_explicit_tomorrow_uses_tomorrow_date():
    """
    Verify Fix 6.4b:
    Explicit "move it to tomorrow at 7 PM" -> uses tomorrow's date (2026-09-13).
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64b_explicit_tomorrow"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_old",
        title="team sync",
        details="2026-09-10 18:00"
    )

    ref_dt = datetime(2026, 9, 12, 12, 0, 0).astimezone() # Today is Saturday Sept 12 -> Tomorrow is Sept 13
    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "move it to tomorrow at 7 PM", chat_id=chat_id, reference_datetime=ref_dt)
    assert res.tool == "schedule_calendar"
    assert res.args["date"] == "2026-09-13"
    assert res.args["start_time"] == "19:00"


def test_15_reschedule_explicit_weekday_uses_requested_date():
    """
    Verify Fix 6.4b:
    Explicit weekday/date "move it to Friday at 7 PM" -> uses requested Friday date (2026-09-18).
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64b_explicit_friday"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_old2",
        title="project review",
        details="2026-09-13 18:00"
    )

    ref_dt = datetime(2026, 9, 12, 12, 0, 0).astimezone() # Today is Saturday Sept 12 -> Next Friday is Sept 18
    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "move it to Friday at 7 PM", chat_id=chat_id, reference_datetime=ref_dt)
    assert res.tool == "schedule_calendar"
    assert res.args["date"] == "2026-09-18"
    assert res.args["start_time"] == "19:00"


# --- FIX 6.4c FOCUSED EXPLICIT TITLE MODIFICATION TESTS ---

def test_16_reschedule_explicit_title_change_updates_title():
    """
    Verify Fix 6.4c (A):
    Existing title "tomorrow for teps" + "make the title teps" -> title "teps".
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64c_title_a"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_101",
        title="tomorrow for teps",
        details="2026-09-13 18:00"
    )

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "make the title teps", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "teps"


def test_17_reschedule_generic_time_only_preserves_existing_title():
    """
    Verify Fix 6.4c (B):
    Existing title "tomorrow for teps" + "make it 7 PM" -> title remains "tomorrow for teps".
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64c_title_b"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_102",
        title="tomorrow for teps",
        details="2026-09-13 18:00"
    )

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "make it 7 PM", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "tomorrow for teps"
    assert res.args["start_time"] == "19:00"


def test_18_reschedule_explicit_title_and_time_updates_title_and_time():
    """
    Verify Fix 6.4c (C):
    Existing title "old title" + "change the title to new title and make it 7 PM" -> title "new title", time 19:00.
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64c_title_c"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_103",
        title="old title",
        details="2026-09-13 18:00"
    )

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "change the title to new title and make it 7 PM", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "new title"
    assert res.args["start_time"] == "19:00"


def test_19_reschedule_explicit_title_time_and_date_preservation():
    """
    Verify Fix 6.4c (D):
    Existing event tomorrow (2026-09-13) + "can u make the title teps and make it 7 pm" -> title "teps", date 2026-09-13, time 19:00.
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64c_title_d"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_104",
        title="tomorrow for teps",
        details="2026-09-13 18:00"
    )

    ref_dt = datetime(2026, 9, 12, 12, 0, 0).astimezone() # Today is Saturday Sept 12; Event is Sunday Sept 13
    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "can u make the title teps and make it 7 pm", chat_id=chat_id, reference_datetime=ref_dt)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "teps"
    assert res.args["date"] == "2026-09-13"
    assert res.args["start_time"] == "19:00"
    assert res.args["event_id"] == "gcal_evt_104"


def test_20_reschedule_rename_it_to_updates_title():
    """
    Verify Fix 6.4c (E):
    "rename it to Team Meeting" -> title "Team Meeting".
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64c_title_e"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_105",
        title="Project Discussion",
        details="2026-09-15 14:00"
    )

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "rename it to Team Meeting", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "Team Meeting"


# --- FIX 6.4d FOCUSED TIME PRESERVATION TESTS ---

def test_21_fix_64d_title_only_modification_preserves_7pm_time():
    """
    Verify Fix 6.4d (A):
    Existing event at 7 PM (19:00) + "make the title phase 6 calendar"
    -> title changed to "phase 6 calendar"
    -> start_time remains 19:00
    -> date remains 2026-09-14
    -> event_id remains gcal_evt_123
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64d_a"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_123",
        title="tomorrow called PhaseCalendar Test",
        details="2026-09-14 19:00"
    )

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "make the title phase 6 calendar", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "phase 6 calendar"
    assert res.args["date"] == "2026-09-14"
    assert res.args["start_time"] == "19:00"
    assert res.args["event_id"] == "gcal_evt_123"


def test_22_fix_64d_title_and_explicit_time_updates_time_to_8pm():
    """
    Verify Fix 6.4d (B):
    Existing event at 7 PM (19:00) + "make the title phase 6 calendar and make it 8 PM"
    -> title changed to "phase 6 calendar"
    -> start_time becomes 20:00
    -> date remains 2026-09-14
    -> event_id remains gcal_evt_123
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64d_b"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_123",
        title="tomorrow called PhaseCalendar Test",
        details="2026-09-14 19:00"
    )

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "make the title phase 6 calendar and make it 8 PM", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "phase 6 calendar"
    assert res.args["date"] == "2026-09-14"
    assert res.args["start_time"] == "20:00"
    assert res.args["event_id"] == "gcal_evt_123"


def test_23_fix_64d_rename_it_to_preserves_7pm_time():
    """
    Verify Fix 6.4d (C):
    Existing event at 7 PM (19:00) + "rename it to New Meeting"
    -> title changes to "New Meeting"
    -> start_time remains 19:00
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64d_c"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_123",
        title="tomorrow called PhaseCalendar Test",
        details="2026-09-14 19:00"
    )

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "rename it to New Meeting", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "New Meeting"
    assert res.args["start_time"] == "19:00"
    assert res.args["event_id"] == "gcal_evt_123"


def test_24_fix_64d_time_only_change_preserves_title_and_updates_time_to_8pm():
    """
    Verify Fix 6.4d (D):
    Existing event at 7 PM (19:00) + "make it 8 PM"
    -> title remains "tomorrow called PhaseCalendar Test"
    -> start_time becomes 20:00
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64d_d"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_123",
        title="tomorrow called PhaseCalendar Test",
        details="2026-09-14 19:00"
    )

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "make it 8 PM", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "tomorrow called PhaseCalendar Test"
    assert res.args["start_time"] == "20:00"
    assert res.args["event_id"] == "gcal_evt_123"


# --- FIX 6.4e FOCUSED INITIAL CREATION TITLE EXTRACTION TESTS ---

def test_25_fix_64e_creation_with_called_clause():
    """
    Verify Fix 6.4e (A):
    "Set an event tomorrow at 6 pm called Phase 6 Calendar Test"
    -> title == "Phase 6 Calendar Test"
    -> date == "2026-09-14"
    -> start_time == "18:00"
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64e_a"
    ref_dt = datetime(2026, 9, 13, 12, 0, 0).astimezone()

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "Set an event tomorrow at 6 pm called Phase 6 Calendar Test", chat_id=chat_id, reference_datetime=ref_dt)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "Phase 6 Calendar Test"
    assert res.args["date"] == "2026-09-14"
    assert res.args["start_time"] == "18:00"
    assert res.args["event_id"] is None


def test_26_fix_64e_creation_with_titled_clause():
    """
    Verify Fix 6.4e (B):
    "Create an event tomorrow at 6 pm titled Team Meeting"
    -> title == "Team Meeting" (no "titled" prefix)
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64e_b"
    ref_dt = datetime(2026, 9, 13, 12, 0, 0).astimezone()

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "Create an event tomorrow at 6 pm titled Team Meeting", chat_id=chat_id, reference_datetime=ref_dt)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "Team Meeting"
    assert res.args["date"] == "2026-09-14"
    assert res.args["start_time"] == "18:00"


def test_27_fix_64e_creation_with_named_clause():
    """
    Verify Fix 6.4e (C):
    "Schedule an event at 7 pm named Project Review"
    -> title == "Project Review" (no "named" prefix)
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64e_c"
    ref_dt = datetime(2026, 9, 13, 12, 0, 0).astimezone()

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "Schedule an event at 7 pm named Project Review", chat_id=chat_id, reference_datetime=ref_dt)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "Project Review"
    assert res.args["start_time"] == "19:00"


def test_28_fix_64e_creation_with_with_title_clause():
    """
    Verify Fix 6.4e (D):
    "Set a calendar event at 8 pm with title Phase 6 Demo"
    -> title == "Phase 6 Demo"
    """
    from datetime import datetime
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64e_d"
    ref_dt = datetime(2026, 9, 13, 12, 0, 0).astimezone()

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "Set a calendar event at 8 pm with title Phase 6 Demo", chat_id=chat_id, reference_datetime=ref_dt)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "Phase 6 Demo"
    assert res.args["start_time"] == "20:00"


def test_29_fix_64e_digit_preservation_in_title():
    """
    Verify Fix 6.4e (E):
    "called Phase 6 Calendar Test" -> "Phase 6 Calendar Test" (preserves digit 6, not "PhaseCalendar Test").
    """
    from agent.core import _extract_explicit_calendar_title
    extracted = _extract_explicit_calendar_title("called Phase 6 Calendar Test")
    assert extracted == "Phase 6 Calendar Test"


def test_30_fix_64e_fix_64d_time_preservation_remains_intact():
    """
    Verify Fix 6.4e (F):
    Existing Fix 6.4d behavior: title-only modification of existing 7 PM event preserves 19:00.
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64e_f"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_777",
        title="old title",
        details="2026-09-14 19:00"
    )

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "make the title phase 6 calendar", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "phase 6 calendar"
    assert res.args["start_time"] == "19:00"
    assert res.args["date"] == "2026-09-14"
    assert res.args["event_id"] == "gcal_evt_777"


# --- FIX 6.4f-1 FOCUSED TIME-RANGE PARSING & RESOLUTION TESTS ---

def test_31_fix_64f1_single_pm_suffix_range():
    """
    Verify Fix 6.4f-1 (A):
    "7-8 PM" -> start_time "19:00", duration 60 mins.
    """
    from agent.core import parse_time_range
    res = parse_time_range("7-8 PM")
    assert res == ("19:00", 60)


def test_32_fix_64f1_single_pm_suffix_with_to():
    """
    Verify Fix 6.4f-1 (B):
    "7 to 8 PM" -> start_time "19:00", duration 60 mins.
    """
    from agent.core import parse_time_range
    res = parse_time_range("7 to 8 PM")
    assert res == ("19:00", 60)


def test_33_fix_64f1_dual_pm_range():
    """
    Verify Fix 6.4f-1 (C):
    "7 PM to 8 PM" -> start_time "19:00", duration 60 mins.
    """
    from agent.core import parse_time_range
    res = parse_time_range("7 PM to 8 PM")
    assert res == ("19:00", 60)


def test_34_fix_64f1_24_hour_range():
    """
    Verify Fix 6.4f-1 (D):
    "19:00-20:00" -> start_time "19:00", duration 60 mins.
    """
    from agent.core import parse_time_range
    res = parse_time_range("19:00-20:00")
    assert res == ("19:00", 60)


def test_35_fix_64f1_bare_range_with_pm_context():
    """
    Verify Fix 6.4f-1 (E):
    Bare "7-8" with context_start_time="19:00" -> start_time "19:00", duration 60 mins.
    """
    from agent.core import parse_time_range
    res = parse_time_range("7-8", context_start_time="19:00")
    assert res == ("19:00", 60)


def test_36_fix_64f1_bare_range_with_am_context():
    """
    Verify Fix 6.4f-1 (F):
    Bare "7-8" with context_start_time="07:00" -> start_time "07:00", duration 60 mins.
    """
    from agent.core import parse_time_range
    res = parse_time_range("7-8", context_start_time="07:00")
    assert res == ("07:00", 60)


def test_37_fix_64f1_bare_range_without_context():
    """
    Verify Fix 6.4f-1 (G):
    Bare "7-8" without context_start_time -> returns None (safe indeterminate).
    """
    from agent.core import parse_time_range
    res = parse_time_range("7-8", context_start_time=None)
    assert res is None


def test_38_fix_64f1_digit_safety_in_non_time_titles():
    """
    Verify Fix 6.4f-1 (H):
    "Phase 6 Calendar Test" -> returns None (must not be parsed as a time range).
    "chapters 7-8" -> returns None.
    """
    from agent.core import parse_time_range
    assert parse_time_range("Phase 6 Calendar Test") is None
    assert parse_time_range("chapters 7-8") is None


def test_39_fix_64f1_title_only_modification_preserves_7pm_time():
    """
    Verify Fix 6.4f-1 (I):
    Fix 6.4d behavior remains intact: title-only modification preserves existing 7 PM time.
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_fix_64f1_i"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_888",
        title="original title",
        details="2026-09-14 19:00"
    )

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "make the title phase 6 calendar", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "phase 6 calendar"
    assert res.args["start_time"] == "19:00"
    assert res.args["date"] == "2026-09-14"
    assert res.args["event_id"] == "gcal_evt_888"


# --- FIX 6.4f-2 CONVERSATIONAL TIME-RANGE ROUTING TESTS ---

def test_40_fix_64f2_context_plus_the_time_is_7_8():
    """
    Verify Fix 6.4f-2 (A):
    Existing calendar context + "the time is 7-8"
    -> schedule_calendar, 19:00, 60 minutes, same event_id.
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_64f2_a"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_123",
        title="Phase 6 Calendar Test",
        details="Date: 2026-09-14, Time: 19:00"
    )

    tc = ToolCall(tool="export_contacts", args={})
    res = _post_process_contact_intent(tc, "the time is 7-8", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "Phase 6 Calendar Test"
    assert res.args["date"] == "2026-09-14"
    assert res.args["start_time"] == "19:00"
    assert res.args["duration_minutes"] == 60
    assert res.args["event_id"] == "gcal_evt_123"


def test_41_fix_64f2_context_plus_the_time_is_7_to_8():
    """
    Verify Fix 6.4f-2 (B):
    Existing calendar context + "the time is 7 to 8" -> same behavior.
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_64f2_b"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_123",
        title="Phase 6 Calendar Test",
        details="Date: 2026-09-14, Time: 19:00"
    )

    tc = ToolCall(tool="export_contacts", args={})
    res = _post_process_contact_intent(tc, "the time is 7 to 8", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "Phase 6 Calendar Test"
    assert res.args["date"] == "2026-09-14"
    assert res.args["start_time"] == "19:00"
    assert res.args["duration_minutes"] == 60
    assert res.args["event_id"] == "gcal_evt_123"


def test_42_fix_64f2_context_plus_the_time_should_be_7_8():
    """
    Verify Fix 6.4f-2 (C):
    Existing calendar context + "the time should be 7-8" -> same behavior.
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_64f2_c"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_123",
        title="Phase 6 Calendar Test",
        details="Date: 2026-09-14, Time: 19:00"
    )

    tc = ToolCall(tool="export_contacts", args={})
    res = _post_process_contact_intent(tc, "the time should be 7-8", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "Phase 6 Calendar Test"
    assert res.args["date"] == "2026-09-14"
    assert res.args["start_time"] == "19:00"
    assert res.args["duration_minutes"] == 60
    assert res.args["event_id"] == "gcal_evt_123"


def test_43_fix_64f2_context_plus_make_the_time_7_8():
    """
    Verify Fix 6.4f-2 (D):
    Existing calendar context + "make the time 7-8" -> same behavior.
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_64f2_d"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_123",
        title="Phase 6 Calendar Test",
        details="Date: 2026-09-14, Time: 19:00"
    )

    tc = ToolCall(tool="export_contacts", args={})
    res = _post_process_contact_intent(tc, "make the time 7-8", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "Phase 6 Calendar Test"
    assert res.args["date"] == "2026-09-14"
    assert res.args["start_time"] == "19:00"
    assert res.args["duration_minutes"] == 60
    assert res.args["event_id"] == "gcal_evt_123"


def test_44_fix_64f2_no_context_plus_the_time_is_7_8():
    """
    Verify Fix 6.4f-2 (E):
    No calendar context + "the time is 7-8" -> must NOT create/update an event.
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_64f2_e_no_ctx"

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "the time is 7-8", chat_id=chat_id)
    assert res.tool != "schedule_calendar"


def test_45_fix_64f2_task_request_protection():
    """
    Verify Fix 6.4f-2 (F):
    "Add chapters 7-8 to my tasks" -> must NOT be intercepted as calendar.
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_64f2_f"

    tc = ToolCall(tool="add_task", args={"title": "chapters 7-8"})
    res = _post_process_contact_intent(tc, "Add chapters 7-8 to my tasks", chat_id=chat_id)
    assert res.tool == "add_task"
    assert res.args["title"] == "chapters 7-8"


def test_46_fix_64f2_alarm_reminder_protection():
    """
    Verify Fix 6.4f-2 (G):
    Alarm/reminder range request -> must NOT be intercepted as calendar.
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_64f2_g"

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "Set an alarm for 7-8 PM", chat_id=chat_id)
    assert res.tool == "set_alarm"


def test_47_fix_64f2_title_date_event_id_preservation():
    """
    Verify Fix 6.4f-2 (H):
    Existing title/date/event_id preservation during range rescheduling.
    """
    from agent.core import _post_process_contact_intent
    chat_id = "chat_64f2_h"
    db.save_context_entity(
        chat_id=chat_id,
        entity_type="calendar",
        entity_id="gcal_evt_999",
        title="Team Standup",
        details="Date: 2026-10-01, Time: 09:00"
    )

    tc = ToolCall(tool="none", args={})
    res = _post_process_contact_intent(tc, "change the time to 10-11", chat_id=chat_id)
    assert res.tool == "schedule_calendar"
    assert res.args["title"] == "Team Standup"
    assert res.args["date"] == "2026-10-01"
    assert res.args["start_time"] == "10:00"
    assert res.args["duration_minutes"] == 60
    assert res.args["event_id"] == "gcal_evt_999"







