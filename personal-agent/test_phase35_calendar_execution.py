# personal-agent/test_phase35_calendar_execution.py
import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os

from agent.schemas import ToolCall, TaskPlan
from main import (
    handle_message,
    execute_single_tool_call,
    execute_task_plan,
    handle_callback_query,
)
import db.session as db


class TestPhase35CalendarExecution(unittest.TestCase):
    """Focused unit tests for Phase 3.5 Calendar Dispatch & Execution Integration."""

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db_path = self.tmp_db.name
        self.tmp_db.close()

        self.db_patch = patch("db.session.DB_PATH", self.tmp_db_path)
        self.db_patch.start()
        db.init_db()

        self.chat_id = "test_chat_35"

    def tearDown(self):
        self.db_patch.stop()
        if os.path.exists(self.tmp_db_path):
            try:
                os.remove(self.tmp_db_path)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # Schedule Calendar Tests
    # ------------------------------------------------------------------ #

    @patch("main.send_telegram_message")
    @patch("main.create_event")
    def test_schedule_calendar_dispatched_with_confirmation(self, mock_create, mock_telegram):
        """1 & 3: schedule_calendar presents confirmation UI and does NOT call create_event immediately."""
        tool_call = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "RAG Meeting",
                "date": "2026-10-20",
                "start_time": "15:00",
                "duration_minutes": 60,
                "attendees": ["john@example.com"],
            },
            reasoning="Schedule meeting",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule RAG meeting Friday at 3 PM")

        # create_event must NOT be called before confirmation
        mock_create.assert_not_called()

        # Confirmation Telegram message must be sent with inline keyboard
        mock_telegram.assert_called()
        call_kwargs = mock_telegram.call_args_list[-1][1]
        reply_markup = call_kwargs.get("reply_markup", {})
        inline_keyboard = reply_markup.get("inline_keyboard", [[]])
        callback_data = inline_keyboard[0][0].get("callback_data", "")

        self.assertTrue(callback_data.startswith("confirm_cal:"))

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.create_event")
    def test_confirm_schedule_calendar_executes_create_event(self, mock_create, mock_answer, mock_telegram):
        """2 & 4: Confirming schedule_calendar calls create_event exactly once with correct args."""
        mock_create.return_value = {
            "status": "success",
            "id": "evt_999",
            "title": "RAG Meeting",
            "start": "2026-10-20T15:00:00+05:30",
            "end": "2026-10-20T16:00:00+05:30",
            "htmlLink": "https://calendar.google.com/event?id=999",
        }

        # First present confirmation
        tool_call = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "RAG Meeting",
                "date": "2026-10-20",
                "start_time": "15:00",
                "duration_minutes": 60,
                "attendees": ["john@example.com"],
            },
            reasoning="Schedule meeting",
        )
        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule RAG meeting Friday at 3 PM")

        # Retrieve action_id from reply_markup
        call_kwargs = mock_telegram.call_args_list[-1][1]
        action_id = call_kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]

        # Trigger confirmation callback
        cq = {
            "id": "cq_cal_1",
            "data": f"confirm_cal:{action_id}",
            "message": {"chat": {"id": self.chat_id}},
        }
        handle_callback_query(cq)

        mock_create.assert_called_once_with(
            title="RAG Meeting",
            date="2026-10-20",
            start_time="15:00",
            duration_minutes=60,
            attendees=["john@example.com"],
        )
        self.assertIsNone(db.get_pending_action(action_id))

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.create_event")
    def test_cancel_schedule_calendar_prevents_create_event(self, mock_create, mock_answer, mock_telegram):
        """5: Cancelling schedule_calendar deletes pending action and prevents create_event call."""
        tool_call = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "RAG Meeting",
                "date": "2026-10-20",
                "start_time": "15:00",
                "duration_minutes": 30,
            },
            reasoning="Schedule meeting",
        )
        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule RAG meeting Friday at 3 PM")

        call_kwargs = mock_telegram.call_args_list[-1][1]
        action_id = call_kwargs["reply_markup"]["inline_keyboard"][0][1]["callback_data"].split(":")[1]

        cq = {
            "id": "cq_cal_cancel",
            "data": f"cancel_cal:{action_id}",
            "message": {"chat": {"id": self.chat_id}},
        }
        handle_callback_query(cq)

        mock_create.assert_not_called()
        self.assertIsNone(db.get_pending_action(action_id))

    @patch("main.create_event")
    def test_calendar_error_handling_sanitized(self, mock_create):
        """6: Errors during create_event execution are handled safely without leaking secrets."""
        mock_create.side_effect = Exception("OAuth refresh_token secret_bearer_token_123 failed")

        tc = ToolCall(
            tool="schedule_calendar",
            args={"title": "Sync", "date": "2026-10-20", "start_time": "10:00", "duration_minutes": 30},
            reasoning="Sync",
        )
        success, msg = execute_single_tool_call(tc, self.chat_id)

        self.assertFalse(success)
        self.assertNotIn("secret_bearer_token_123", msg)
        self.assertIn("Execution error", msg)

    # ------------------------------------------------------------------ #
    # List Calendar Tests
    # ------------------------------------------------------------------ #

    @patch("main.send_telegram_message")
    @patch("main.list_upcoming_events")
    def test_list_calendar_dispatches_without_confirmation(self, mock_list, mock_telegram):
        """7, 8 & 9: list_calendar executes directly without confirmation, passing datetime window."""
        mock_list.return_value = [
            {
                "id": "e1",
                "title": "RAG Meeting",
                "start": "2026-10-20T15:00:00+05:30",
                "end": "2026-10-20T16:00:00+05:30",
                "attendees": [],
            }
        ]

        tool_call = ToolCall(
            tool="list_calendar",
            args={
                "start_datetime": "2026-10-20T00:00:00+05:30",
                "end_datetime": "2026-10-20T23:59:59+05:30",
            },
            reasoning="List events",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "What's on my calendar tomorrow?")

        mock_list.assert_called_once_with(
            start_datetime="2026-10-20T00:00:00+05:30",
            end_datetime="2026-10-20T23:59:59+05:30",
        )

        # Telegram message sent containing formatted events
        mock_telegram.assert_called()
        last_msg = mock_telegram.call_args_list[-1][0][0]
        self.assertIn("RAG Meeting", last_msg)

    @patch("main.send_telegram_message")
    @patch("main.list_upcoming_events")
    def test_list_calendar_empty_results(self, mock_list, mock_telegram):
        """10: Empty list_calendar results produce clean user message."""
        mock_list.return_value = []

        tc = ToolCall(tool="list_calendar", args={}, reasoning="List events")
        success, msg = execute_single_tool_call(tc, self.chat_id)

        self.assertTrue(success)
        self.assertIn("no calendar events", msg)

    @patch("main.list_upcoming_events")
    def test_list_calendar_api_error_handled_safely(self, mock_list):
        """11: API errors during list_calendar are handled safely."""
        mock_list.side_effect = Exception("OAuth connection reset secret_token_abc")

        tc = ToolCall(tool="list_calendar", args={}, reasoning="List events")
        success, msg = execute_single_tool_call(tc, self.chat_id)

        self.assertFalse(success)
        self.assertNotIn("secret_token_abc", msg)

    # ------------------------------------------------------------------ #
    # TaskPlan Compatibility Tests
    # ------------------------------------------------------------------ #

    @patch("main.send_telegram_message")
    @patch("main.create_event")
    def test_taskplan_schedule_calendar_confirmation_preserved(self, mock_create, mock_telegram):
        """12 & 13: TaskPlan with schedule_calendar defers execution until user confirms plan."""
        plan = TaskPlan(
            tasks=[
                ToolCall(
                    tool="schedule_calendar",
                    args={"title": "Sync", "date": "2026-10-20", "start_time": "15:00", "duration_minutes": 30},
                    reasoning="Schedule sync",
                ),
                ToolCall(
                    tool="send_email",
                    args={"to": "john@example.com", "subject": "Sync", "body": "Hi John"},
                    reasoning="Notify John",
                ),
            ],
            reasoning="Schedule meeting and notify John",
        )

        with patch("main.call_agent", return_value=plan):
            handle_message(self.chat_id, "Schedule sync Friday at 3pm and email john@example.com")

        # create_event must NOT be called yet
        mock_create.assert_not_called()

        # TaskPlan confirmation UI presented
        call_kwargs = mock_telegram.call_args_list[-1][1]
        inline_keyboard = call_kwargs["reply_markup"]["inline_keyboard"]
        self.assertTrue(inline_keyboard[0][0]["callback_data"].startswith("plan_execute:"))


if __name__ == "__main__":
    unittest.main()
