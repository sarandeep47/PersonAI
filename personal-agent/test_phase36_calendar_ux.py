# personal-agent/test_phase36_calendar_ux.py
import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os

from agent.schemas import ToolCall
from main import (
    handle_message,
    handle_callback_query,
    _format_calendar_datetime_range,
    _format_calendar_success_message,
)
import db.session as db


class TestPhase36CalendarUX(unittest.TestCase):
    """Focused unit tests for Phase 3.6 Calendar Confirmation UX and Safety."""

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db_path = self.tmp_db.name
        self.tmp_db.close()

        self.db_patch = patch("db.session.DB_PATH", self.tmp_db_path)
        self.db_patch.start()
        db.init_db()

        self.chat_id = "test_chat_36"

    def tearDown(self):
        self.db_patch.stop()
        if os.path.exists(self.tmp_db_path):
            try:
                os.remove(self.tmp_db_path)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # Requirement 1 & 6: Confirmation message contains title & buttons
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.create_event")
    def test_confirmation_message_contains_title_and_buttons(self, mock_create, mock_telegram):
        """1 & 6: Confirmation message contains title and Confirm/Cancel buttons."""
        tool_call = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "RAG project meeting",
                "date": "2026-09-11",
                "start_time": "15:00",
                "duration_minutes": 60,
                "attendees": ["john@example.com"],
            },
            reasoning="Schedule RAG meeting",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule RAG project meeting Friday at 3 PM for 1 hour with john@example.com")

        mock_create.assert_not_called()
        mock_telegram.assert_called()

        msg_text = mock_telegram.call_args_list[-1][0][0]
        reply_markup = mock_telegram.call_args_list[-1][1].get("reply_markup", {})
        inline_keyboard = reply_markup.get("inline_keyboard", [[]])

        self.assertIn("*Title:* RAG project meeting", msg_text)
        self.assertEqual(len(inline_keyboard[0]), 2)
        self.assertEqual(inline_keyboard[0][0]["text"], "✅ Confirm")
        self.assertTrue(inline_keyboard[0][0]["callback_data"].startswith("confirm_cal:"))
        self.assertEqual(inline_keyboard[0][1]["text"], "❌ Cancel")
        self.assertTrue(inline_keyboard[0][1]["callback_data"].startswith("cancel_cal:"))

    # ------------------------------------------------------------------ #
    # Requirement 2 & 3: Confirmation message contains formatted date and time
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.create_event")
    def test_confirmation_message_contains_date_and_time_range(self, mock_create, mock_telegram):
        """2 & 3: Confirmation message contains formatted date and start/end time range."""
        tool_call = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "Strategy Sync",
                "date": "2026-09-11",
                "start_time": "15:00",
                "duration_minutes": 60,
            },
            reasoning="Strategy sync",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule Strategy Sync Friday at 3 PM for 1 hour")

        msg_text = mock_telegram.call_args_list[-1][0][0]
        self.assertIn("*Date:* Friday, September 11, 2026", msg_text)
        self.assertIn("*Time:* 3:00 PM – 4:00 PM", msg_text)

    # ------------------------------------------------------------------ #
    # Requirement 4: Attendees appear when provided
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.create_event")
    def test_attendees_included_when_provided(self, mock_create, mock_telegram):
        """4: Attendees appear when provided in tool call arguments."""
        tool_call = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "Team Huddle",
                "date": "2026-09-11",
                "start_time": "10:00",
                "duration_minutes": 30,
                "attendees": ["john@example.com", "jane@example.com"],
            },
            reasoning="Team huddle",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule Team Huddle with john@example.com and jane@example.com")

        msg_text = mock_telegram.call_args_list[-1][0][0]
        self.assertIn("*Attendees:* john@example.com, jane@example.com", msg_text)

    # ------------------------------------------------------------------ #
    # Requirement 5: Attendees are omitted when absent
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.create_event")
    def test_attendees_omitted_when_absent(self, mock_create, mock_telegram):
        """5: Attendees line is completely omitted when no attendees are provided."""
        tool_call = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "Focus Block",
                "date": "2026-09-11",
                "start_time": "14:00",
                "duration_minutes": 60,
            },
            reasoning="Focus block",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule Focus Block Friday at 2 PM")

        msg_text = mock_telegram.call_args_list[-1][0][0]
        self.assertNotIn("Attendees:", msg_text)

    # ------------------------------------------------------------------ #
    # Requirement 7: Successful confirmation produces clean feedback
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.create_event")
    def test_successful_confirmation_feedback(self, mock_create, mock_answer, mock_telegram):
        """7: Confirming event creation produces clean, formatted success feedback."""
        mock_create.return_value = {
            "status": "success",
            "id": "evt_123",
            "title": "RAG project meeting",
            "start": "2026-09-11T15:00:00+05:30",
            "end": "2026-09-11T16:00:00+05:30",
            "htmlLink": "https://calendar.google.com/event?id=123",
        }

        tool_call = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "RAG project meeting",
                "date": "2026-09-11",
                "start_time": "15:00",
                "duration_minutes": 60,
            },
            reasoning="Schedule event",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule meeting")

        action_id = mock_telegram.call_args_list[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]

        cq = {
            "id": "cq_confirm_1",
            "data": f"confirm_cal:{action_id}",
            "message": {"chat": {"id": self.chat_id}},
        }
        handle_callback_query(cq)

        mock_create.assert_called_once()
        last_msg = mock_telegram.call_args_list[-1][0][0]
        self.assertIn("✅ *Calendar event created*", last_msg)
        self.assertIn("*RAG project meeting*", last_msg)
        self.assertIn("Friday, September 11", last_msg)
        self.assertIn("3:00 PM – 4:00 PM", last_msg)
        self.assertIn("🔗 [Open in Google Calendar]", last_msg)

    # ------------------------------------------------------------------ #
    # Requirement 8: Cancellation produces clean feedback
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.create_event")
    def test_cancellation_feedback(self, mock_create, mock_answer, mock_telegram):
        """8: Cancelling produces clean cancellation feedback and deletes pending action."""
        tool_call = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "Cancelled Meeting",
                "date": "2026-09-11",
                "start_time": "11:00",
                "duration_minutes": 30,
            },
            reasoning="Cancelled meeting",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule meeting")

        action_id = mock_telegram.call_args_list[-1][1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"].split(":")[1]

        cq = {
            "id": "cq_cancel_1",
            "data": f"cancel_cal:{action_id}",
            "message": {"chat": {"id": self.chat_id}},
        }
        handle_callback_query(cq)

        mock_create.assert_not_called()
        last_msg = mock_telegram.call_args_list[-1][0][0]
        self.assertIn("❌ *Calendar event cancelled*", last_msg)
        self.assertIsNone(db.get_pending_action(action_id))

    # ------------------------------------------------------------------ #
    # Requirement 9: Long titles do not crash formatting
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.create_event")
    def test_long_titles_do_not_crash_formatting(self, mock_create, mock_telegram):
        """9: Confirmation formatting is robust for very long event titles."""
        long_title = "Quarterly Strategic Architecture Roadmap Alignment & Multi-Regional Team Synchronization Workshop"
        tool_call = ToolCall(
            tool="schedule_calendar",
            args={
                "title": long_title,
                "date": "2026-09-11",
                "start_time": "14:00",
                "duration_minutes": 120,
            },
            reasoning="Long title test",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule workshop")

        msg_text = mock_telegram.call_args_list[-1][0][0]
        self.assertIn(f"*Title:* {long_title}", msg_text)
        self.assertIn("*Time:* 2:00 PM – 4:00 PM", msg_text)

    # ------------------------------------------------------------------ #
    # Requirement 10: Duplicate confirmation protection still works
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.create_event")
    def test_duplicate_confirmation_protection(self, mock_create, mock_answer, mock_telegram):
        """10: Clicking confirmation button twice fails safely without duplicating execution."""
        mock_create.return_value = {
            "status": "success",
            "id": "evt_dup",
            "title": "Dup Test",
            "start": "2026-09-11T15:00:00+05:30",
            "end": "2026-09-11T16:00:00+05:30",
        }

        tool_call = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "Dup Test",
                "date": "2026-09-11",
                "start_time": "15:00",
                "duration_minutes": 60,
            },
            reasoning="Dup test",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule meeting")

        action_id = mock_telegram.call_args_list[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]

        cq = {
            "id": "cq_dup_1",
            "data": f"confirm_cal:{action_id}",
            "message": {"chat": {"id": self.chat_id}},
        }
        handle_callback_query(cq)
        self.assertEqual(mock_create.call_count, 1)

        # Second click on same action_id
        cq_dup = {
            "id": "cq_dup_2",
            "data": f"confirm_cal:{action_id}",
            "message": {"chat": {"id": self.chat_id}},
        }
        handle_callback_query(cq_dup)

        # create_event must NOT be called a second time
        self.assertEqual(mock_create.call_count, 1)
        last_msg = mock_telegram.call_args_list[-1][0][0]
        self.assertIn("expired", last_msg)

    # ------------------------------------------------------------------ #
    # Helper Function Unit Tests (12h/24h time, ISO date parsing)
    # ------------------------------------------------------------------ #
    def test_format_calendar_datetime_range_variations(self):
        """Test date and time range formatting helper for various input styles."""
        # 24-hour time input
        d1, t1 = _format_calendar_datetime_range("2026-09-11", "15:00", 60)
        self.assertEqual(d1, "Friday, September 11, 2026")
        self.assertEqual(t1, "3:00 PM – 4:00 PM")

        # 12-hour time input with AM/PM
        d2, t2 = _format_calendar_datetime_range("2026-09-11", "3:00 PM", 30)
        self.assertEqual(d2, "Friday, September 11, 2026")
        self.assertEqual(t2, "3:00 PM – 3:30 PM")

        # ISO date representation
        d3, t3 = _format_calendar_datetime_range("2026-09-11", "09:00:00", 45)
        self.assertEqual(d3, "Friday, September 11, 2026")
        self.assertEqual(t3, "9:00 AM – 9:45 AM")


if __name__ == "__main__":
    unittest.main()
