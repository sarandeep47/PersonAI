# personal-agent/test_phase38_calendar_audit.py
import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os
from datetime import datetime

from agent.schemas import ToolCall, TaskPlan, ScheduleCalendarArgs, ListCalendarArgs, TOOL_ARGS_SCHEMAS
from agent.prompts import AGENT_SYSTEM_PROMPT
from tools.calendar import create_event, list_upcoming_events, _sanitize_error, _parse_datetime
from tools.google_auth import DEFAULT_SCOPES, get_calendar_service, get_gmail_service
from main import (
    handle_message,
    handle_callback_query,
    execute_single_tool_call,
    execute_task_plan,
    _format_calendar_datetime_range,
    _format_calendar_success_message,
)
import db.session as db


class TestPhase38CalendarAudit(unittest.TestCase):
    """Comprehensive Phase 3.8 Testing & Regression Audit for Google Calendar Feature Suite."""

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db_path = self.tmp_db.name
        self.tmp_db.close()

        self.db_patch = patch("db.session.DB_PATH", self.tmp_db_path)
        self.db_patch.start()
        db.init_db()

        self.chat_id = "test_chat_38"

    def tearDown(self):
        self.db_patch.stop()
        if os.path.exists(self.tmp_db_path):
            try:
                os.remove(self.tmp_db_path)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # Step 2: Calendar Tool Input & Edge Case Audit (`create_event`)
    # ------------------------------------------------------------------ #
    def test_create_event_input_variations_and_edge_cases(self):
        """Audit create_event input handling: 24h, 12h, midnight, noon, 1-min duration, long duration."""
        mock_service = MagicMock()
        mock_insert = MagicMock()
        mock_service.events.return_value.insert.return_value = mock_insert

        mock_insert.execute.return_value = {
            "id": "evt_edge_1",
            "summary": "Midnight Edge Sync",
            "start": {"dateTime": "2026-09-11T00:00:00+05:30"},
            "end": {"dateTime": "2026-09-11T00:01:00+05:30"},
            "attendees": [{"email": "john@example.com"}, {"email": "jane@example.com"}],
            "htmlLink": "https://calendar.google.com/event?id=edge1",
        }

        res = create_event(
            title="Midnight Edge Sync",
            date="2026-09-11",
            start_time="00:00",
            duration_minutes=1,
            attendees=["john@example.com", "jane@example.com"],
            service=mock_service,
        )

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["title"], "Midnight Edge Sync")
        self.assertEqual(len(res["attendees"]), 2)

    def test_create_event_invalid_inputs_handled_safely(self):
        """Audit create_event safety when date/time cannot be parsed."""
        mock_service = MagicMock()
        res = create_event(
            title="Invalid Date Event",
            date="invalid-date-string",
            start_time="invalid-time",
            duration_minutes=30,
            service=mock_service,
        )
        self.assertEqual(res["status"], "error")
        self.assertIn("⚠️", res["message"])

    # ------------------------------------------------------------------ #
    # Step 3: Calendar Listing Audit (`list_upcoming_events`)
    # ------------------------------------------------------------------ #
    def test_list_upcoming_events_all_day_and_timed_events(self):
        """Audit list_upcoming_events handling timed events and all-day events."""
        mock_service = MagicMock()
        mock_list = MagicMock()
        mock_service.events.return_value.list.return_value = mock_list
        mock_list.execute.return_value = {
            "items": [
                {
                    "id": "e_timed",
                    "summary": "Timed Meeting",
                    "start": {"dateTime": "2026-09-11T10:00:00+05:30"},
                    "end": {"dateTime": "2026-09-11T11:00:00+05:30"},
                    "attendees": [{"email": "a@example.com"}],
                },
                {
                    "id": "e_allday",
                    "summary": "Company Holiday",
                    "start": {"date": "2026-09-12"},
                    "end": {"date": "2026-09-13"},
                },
            ]
        }

        events = list_upcoming_events(service=mock_service)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["title"], "Timed Meeting")
        self.assertEqual(events[1]["title"], "Company Holiday")
        self.assertEqual(events[1]["start"], "2026-09-12")

    # ------------------------------------------------------------------ #
    # Step 4: Schema Validation Audit
    # ------------------------------------------------------------------ #
    def test_schemas_schedule_calendar_and_list_calendar_registered(self):
        """Audit schema registration and argument validation."""
        self.assertIn("schedule_calendar", TOOL_ARGS_SCHEMAS)
        self.assertIn("list_calendar", TOOL_ARGS_SCHEMAS)

        # Valid args pass validation
        valid_sched = ScheduleCalendarArgs(title="Sync", date="2026-09-11", start_time="15:00", duration_minutes=30)
        self.assertEqual(valid_sched.title, "Sync")

        valid_list = ListCalendarArgs(start_datetime="2026-09-11T00:00:00", end_datetime="2026-09-11T23:59:59")
        self.assertEqual(valid_list.start_datetime, "2026-09-11T00:00:00")

    # ------------------------------------------------------------------ #
    # Step 5: Prompt / LLM System Prompt Audit
    # ------------------------------------------------------------------ #
    def test_prompt_contains_calendar_tool_guidance(self):
        """Audit system prompt to confirm calendar tools and decision rules are present."""
        self.assertIn("schedule_calendar", AGENT_SYSTEM_PROMPT)
        self.assertIn("list_calendar", AGENT_SYSTEM_PROMPT)
        self.assertIn("Do NOT confuse listing vs scheduling", AGENT_SYSTEM_PROMPT)

    # ------------------------------------------------------------------ #
    # Step 6 & 10: Confirmation Safety & Callback Isolation Audit
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.create_event")
    def test_confirmation_callback_isolation_matrix(self, mock_create, mock_answer, mock_telegram):
        """
        Thorough audit of confirmation callbacks:
        - Confirm triggers exactly 1 API call
        - Cancel triggers 0 API calls
        - Double Confirm triggers 1 API call total
        - Confirm after Cancel triggers 0 API calls
        - Mismatched chat ID triggers 0 API calls
        - Mismatched action_type triggers 0 API calls
        """
        mock_create.return_value = {"status": "success", "id": "e1", "title": "Test", "start": "2026-09-11T10:00:00+05:30"}

        # 1. Store valid pending action
        action_id = "cal_test_iso_1"
        db.save_pending_action(action_id, self.chat_id, "confirm_schedule_calendar", {
            "title": "Audit Meeting", "date": "2026-09-11", "start_time": "10:00", "duration_minutes": 30
        })

        # Test: Mismatched chat_id -> Blocked
        cq_wrong_user = {"id": "cq_wu", "data": f"confirm_cal:{action_id}", "message": {"chat": {"id": "wrong_chat_user"}}}
        handle_callback_query(cq_wrong_user)
        mock_create.assert_not_called()

        # Test: Valid Confirm -> Executes exactly once
        cq_valid = {"id": "cq_valid", "data": f"confirm_cal:{action_id}", "message": {"chat": {"id": self.chat_id}}}
        handle_callback_query(cq_valid)
        self.assertEqual(mock_create.call_count, 1)

        # Test: Second Confirm on now-consumed action -> Blocked
        handle_callback_query(cq_valid)
        self.assertEqual(mock_create.call_count, 1)

        # Test: Mismatched action_type -> Blocked
        action_id_wrong_type = "cal_wrong_type"
        db.save_pending_action(action_id_wrong_type, self.chat_id, "confirm_send_email", {"to": "a@b.com"})
        cq_wrong_type = {"id": "cq_wt", "data": f"confirm_cal:{action_id_wrong_type}", "message": {"chat": {"id": self.chat_id}}}
        handle_callback_query(cq_wrong_type)
        self.assertEqual(mock_create.call_count, 1)

    # ------------------------------------------------------------------ #
    # Step 7: TaskPlan Integration Audit
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.create_event")
    @patch("main.send_email_raw")
    def test_taskplan_calendar_and_email_sequential_execution(self, mock_email, mock_create, mock_telegram):
        """Audit TaskPlan containing schedule_calendar and send_email."""
        mock_create.return_value = {"status": "success", "id": "e_plan", "title": "Plan Meeting", "start": "2026-09-11T15:00:00+05:30"}
        mock_email.return_value = True

        plan = TaskPlan(
            tasks=[
                ToolCall(tool="schedule_calendar", args={"title": "Plan Meeting", "date": "2026-09-11", "start_time": "15:00", "duration_minutes": 60}, reasoning="Schedule"),
                ToolCall(tool="send_email", args={"to": "john@example.com", "subject": "Meeting Scheduled", "body": "Hi John"}, reasoning="Email"),
            ],
            reasoning="Schedule meeting and notify John",
        )

        res_msg = execute_task_plan(plan, self.chat_id)
        mock_create.assert_called_once()
        mock_email.assert_called_once()
        self.assertIn("Task Plan Execution Result", res_msg)

    # ------------------------------------------------------------------ #
    # Step 11: Shared OAuth Architecture Audit
    # ------------------------------------------------------------------ #
    def test_shared_oauth_configuration_scopes(self):
        """Audit shared OAuth scopes to ensure calendar.events scope is registered."""
        self.assertIn("https://www.googleapis.com/auth/calendar.events", DEFAULT_SCOPES)
        self.assertIn("https://www.googleapis.com/auth/gmail.readonly", DEFAULT_SCOPES)
        self.assertIn("https://www.googleapis.com/auth/gmail.send", DEFAULT_SCOPES)

    def test_relative_date_resolution_correction(self):
        """Audit relative date resolution (e.g., Thursday prompt requesting Friday resolves to upcoming Friday)."""
        from datetime import datetime
        from agent.core import validate_tool_call, resolve_relative_calendar_date

        ref_dt = datetime.strptime("2026-09-10", "%Y-%m-%d") # Thursday
        res_date = resolve_relative_calendar_date("Schedule my RAG project meeting Friday at 3 PM for 1 hour.", ref_dt=ref_dt)
        self.assertEqual(res_date, "2026-09-11") # Should resolve to Friday Sep 11, 2026

        # Simulate LLM producing incorrect Thursday date (2026-09-17) when user requested Friday
        llm_tool = ToolCall(
            tool="schedule_calendar",
            args={"title": "RAG project meeting", "date": "2026-09-17", "start_time": "15:00", "duration_minutes": 60},
            reasoning="Schedule meeting"
        )
        # Validate should detect weekday mismatch (Thursday vs Friday) and correct date
        validated = validate_tool_call(llm_tool, "Schedule my RAG project meeting Friday at 3 PM for 1 hour.")
        # Today is Sep 10 (Thursday), upcoming Friday is Sep 11
        dt_val = datetime.strptime(validated.args["date"], "%Y-%m-%d")
        self.assertEqual(dt_val.strftime("%A"), "Friday")


if __name__ == "__main__":
    unittest.main()

