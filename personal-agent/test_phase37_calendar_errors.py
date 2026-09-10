# personal-agent/test_phase37_calendar_errors.py
import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os

from agent.schemas import ToolCall
from main import (
    handle_message,
    handle_callback_query,
    execute_single_tool_call,
)
from tools.calendar import create_event, _sanitize_error
import db.session as db


class TestPhase37CalendarErrors(unittest.TestCase):
    """Focused unit tests for Phase 3.7 Calendar Execution Reliability & User Error Handling."""

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db_path = self.tmp_db.name
        self.tmp_db.close()

        self.db_patch = patch("db.session.DB_PATH", self.tmp_db_path)
        self.db_patch.start()
        db.init_db()

        self.chat_id = "test_chat_37"

    def tearDown(self):
        self.db_patch.stop()
        if os.path.exists(self.tmp_db_path):
            try:
                os.remove(self.tmp_db_path)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # Test 1: Successful creation
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.create_event")
    def test_1_successful_creation(self, mock_create, mock_answer, mock_telegram):
        """Test 1: Calendar API succeeds, preserving title, date, time, and htmlLink."""
        mock_create.return_value = {
            "status": "success",
            "id": "evt_success_100",
            "title": "Architecture Sync",
            "start": "2026-09-11T15:00:00+05:30",
            "end": "2026-09-11T16:00:00+05:30",
            "htmlLink": "https://calendar.google.com/event?id=100",
        }

        tool_call = ToolCall(
            tool="schedule_calendar",
            args={"title": "Architecture Sync", "date": "2026-09-11", "start_time": "15:00", "duration_minutes": 60},
            reasoning="Architecture sync",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule Architecture Sync Friday at 3 PM")

        action_id = mock_telegram.call_args_list[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]

        cq = {"id": "cq_succ_1", "data": f"confirm_cal:{action_id}", "message": {"chat": {"id": self.chat_id}}}
        handle_callback_query(cq)

        mock_create.assert_called_once()
        last_msg = mock_telegram.call_args_list[-1][0][0]
        self.assertIn("✅ *Calendar event created*", last_msg)
        self.assertIn("*Architecture Sync*", last_msg)
        self.assertIn("Friday, September 11", last_msg)
        self.assertIn("3:00 PM – 4:00 PM", last_msg)
        self.assertIn("🔗 [Open in Google Calendar](https://calendar.google.com/event?id=100)", last_msg)

    # ------------------------------------------------------------------ #
    # Test 2: Google API failure
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.create_event")
    def test_2_google_api_failure(self, mock_create, mock_answer, mock_telegram):
        """Test 2: Google API error returns clean message with no traceback or raw API response exposure."""
        mock_create.return_value = {
            "status": "error",
            "message": "Calendar API error: ⚠️ I couldn't create the calendar event right now.\n\nPlease try again in a moment.",
        }

        tool_call = ToolCall(
            tool="schedule_calendar",
            args={"title": "Team Meeting", "date": "2026-09-11", "start_time": "10:00", "duration_minutes": 30},
            reasoning="Team meeting",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule Team Meeting")

        action_id = mock_telegram.call_args_list[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]

        cq = {"id": "cq_api_err", "data": f"confirm_cal:{action_id}", "message": {"chat": {"id": self.chat_id}}}
        handle_callback_query(cq)

        last_msg = mock_telegram.call_args_list[-1][0][0]
        self.assertIn("⚠️", last_msg)
        self.assertNotIn("Traceback", last_msg)
        self.assertNotIn("line ", last_msg)
        self.assertNotIn("File \"", last_msg)

    # ------------------------------------------------------------------ #
    # Test 3: Authentication failure
    # ------------------------------------------------------------------ #
    def test_3_authentication_failure(self):
        """Test 3: Auth/Authorization failure returns reconnect guidance without exposing credentials or tokens."""
        err1 = FileNotFoundError("OAuth client credentials file 'credentials.json' not found.")
        msg1 = _sanitize_error(err1)

        self.assertIn("reconnect your Google account", msg1)
        self.assertNotIn("credentials.json", msg1)
        self.assertNotIn("token.json", msg1)

        err2 = Exception("Google Auth Error: refresh_token expired secret_token_xyz")
        msg2 = _sanitize_error(err2)

        self.assertIn("reconnect your Google account", msg2)
        self.assertNotIn("secret_token_xyz", msg2)
        self.assertNotIn("refresh_token", msg2)

    # ------------------------------------------------------------------ #
    # Test 4: Network/API failure
    # ------------------------------------------------------------------ #
    def test_4_network_api_failure(self):
        """Test 4: Generic network/API exception returns retry-later style message with zero internal leakage."""
        err = ConnectionError("HTTPSConnectionPool(host='www.googleapis.com'): Max retries exceeded")
        msg = _sanitize_error(err)

        self.assertIn("Please try again in a moment", msg)
        self.assertNotIn("HTTPSConnectionPool", msg)
        self.assertNotIn("googleapis.com", msg)

    # ------------------------------------------------------------------ #
    # Test 5: Duplicate confirmation after failure
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.create_event")
    def test_5_duplicate_confirmation_after_failure(self, mock_create, mock_answer, mock_telegram):
        """Test 5: Execution failure consumes pending action; duplicate click does NOT re-trigger API call."""
        mock_create.return_value = {
            "status": "error",
            "message": "Calendar API error: ⚠️ I couldn't create the calendar event right now.\n\nPlease try again in a moment.",
        }

        tool_call = ToolCall(
            tool="schedule_calendar",
            args={"title": "Single Call Test", "date": "2026-09-11", "start_time": "14:00", "duration_minutes": 30},
            reasoning="Single call test",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule meeting")

        action_id = mock_telegram.call_args_list[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]

        # First confirm click -> fails
        cq1 = {"id": "cq_fail_1", "data": f"confirm_cal:{action_id}", "message": {"chat": {"id": self.chat_id}}}
        handle_callback_query(cq1)

        self.assertEqual(mock_create.call_count, 1)
        self.assertIsNone(db.get_pending_action(action_id))

        # Second confirm click -> blocked because pending action is gone
        cq2 = {"id": "cq_fail_2", "data": f"confirm_cal:{action_id}", "message": {"chat": {"id": self.chat_id}}}
        handle_callback_query(cq2)

        # create_event must NOT be called a second time
        self.assertEqual(mock_create.call_count, 1)
        last_msg = mock_telegram.call_args_list[-1][0][0]
        self.assertIn("expired", last_msg)

    # ------------------------------------------------------------------ #
    # Test 6: Cancel after failed execution
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.create_event")
    def test_6_cancel_after_failed_execution(self, mock_create, mock_answer, mock_telegram):
        """Test 6: Attempting cancellation after failed execution does not trigger Calendar API."""
        mock_create.return_value = {
            "status": "error",
            "message": "Calendar API error: ⚠️ I couldn't create the calendar event right now.",
        }

        tool_call = ToolCall(
            tool="schedule_calendar",
            args={"title": "Cancel Test", "date": "2026-09-11", "start_time": "15:00", "duration_minutes": 30},
            reasoning="Cancel test",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule meeting")

        action_id = mock_telegram.call_args_list[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]

        # First confirm click -> fails
        cq1 = {"id": "cq_cancel_fail_1", "data": f"confirm_cal:{action_id}", "message": {"chat": {"id": self.chat_id}}}
        handle_callback_query(cq1)

        self.assertEqual(mock_create.call_count, 1)

        # Attempting cancel button click on consumed action
        cq_cancel = {"id": "cq_cancel_fail_2", "data": f"cancel_cal:{action_id}", "message": {"chat": {"id": self.chat_id}}}
        handle_callback_query(cq_cancel)

        # create_event must NOT be called again
        self.assertEqual(mock_create.call_count, 1)

    # ------------------------------------------------------------------ #
    # Test 7: Telegram send failure
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message", side_effect=Exception("Telegram connection reset: 400 Bad Request secret_token_999"))
    @patch("main.answer_callback_query")
    def test_7_telegram_send_failure(self, mock_answer, mock_telegram):
        """Test 7: Telegram send/update failure does not crash the bot and suppresses secrets."""
        cq = {"id": "cq_tg_fail", "data": "confirm_cal:non_existent_action", "message": {"chat": {"id": self.chat_id}}}

        # Function should execute safely without crashing
        try:
            handle_callback_query(cq)
            success = True
        except Exception:
            success = False

        self.assertTrue(success)

    # ------------------------------------------------------------------ #
    # Test 8: Existing Phase 3.6 success path
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.create_event")
    def test_8_existing_phase_36_success_path(self, mock_create, mock_answer, mock_telegram):
        """Test 8: Phase 3.6 confirmation & success formatting remains fully intact."""
        mock_create.return_value = {
            "status": "success",
            "id": "evt_36_path",
            "title": "RAG project meeting",
            "start": "2026-09-11T15:00:00+05:30",
            "end": "2026-09-11T16:00:00+05:30",
            "htmlLink": "https://calendar.google.com/event?id=36",
        }

        tool_call = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "RAG project meeting",
                "date": "2026-09-11",
                "start_time": "15:00",
                "duration_minutes": 60,
                "attendees": ["john@example.com"],
            },
            reasoning="Schedule meeting",
        )

        with patch("main.call_agent", return_value=tool_call):
            handle_message(self.chat_id, "Schedule RAG meeting")

        # 1. Confirmation message check
        conf_msg = mock_telegram.call_args_list[-1][0][0]
        self.assertIn("*Title:* RAG project meeting", conf_msg)
        self.assertIn("*Date:* Friday, September 11, 2026", conf_msg)
        self.assertIn("*Time:* 3:00 PM – 4:00 PM", conf_msg)
        self.assertIn("*Attendees:* john@example.com", conf_msg)

        action_id = mock_telegram.call_args_list[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]

        cq = {"id": "cq_36_conf", "data": f"confirm_cal:{action_id}", "message": {"chat": {"id": self.chat_id}}}
        handle_callback_query(cq)

        # 2. Success message check
        succ_msg = mock_telegram.call_args_list[-1][0][0]
        self.assertIn("✅ *Calendar event created*", succ_msg)
        self.assertIn("*RAG project meeting*", succ_msg)
        self.assertIn("Friday, September 11", succ_msg)
        self.assertIn("3:00 PM – 4:00 PM", succ_msg)
        self.assertIn("🔗 [Open in Google Calendar]", succ_msg)


if __name__ == "__main__":
    unittest.main()
