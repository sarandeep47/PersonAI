import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

import db.session as db
from agent.core import (
    parse_calendar_relative_intent,
    process_calendar_relative_reminder,
    validate_tool_call,
)
from agent.schemas import ToolCall
from main import (
    handle_callback_query,
    check_and_fire_due_alarms,
    alarm_checker_loop,
    _present_alarm_confirmation,
)


class TestPhase48AlarmReliability(unittest.TestCase):
    """Focused unit tests for Phase 4.8 Reminder System Error Handling & Reliability."""

    def setUp(self):
        self.chat_id = "test_user_phase48"
        db.init_db()
        conn = db.get_db()
        with conn:
            conn.execute("DELETE FROM alarms WHERE chat_id = ?", (self.chat_id,))
            conn.execute("DELETE FROM pending_actions WHERE chat_id = ?", (self.chat_id,))

    def tearDown(self):
        conn = db.get_db()
        with conn:
            conn.execute("DELETE FROM alarms WHERE chat_id = ?", (self.chat_id,))
            conn.execute("DELETE FROM pending_actions WHERE chat_id = ?", (self.chat_id,))

    # --- 1. Database Exception Handling Tests ---

    def test_01_db_exception_during_save_alarm_handled(self):
        """1. Database exception during save_alarm raises RuntimeError with safe log."""
        with patch("db.session.get_db", side_effect=Exception("Database lock error")):
            with self.assertRaises(RuntimeError):
                db.save_alarm(self.chat_id, "Test message", 1750000000.0)

    def test_02_db_exception_during_get_pending_alarms_returns_empty_list(self):
        """2. Database exception during get_pending_alarms returns empty list safely."""
        with patch("db.session.get_db", side_effect=Exception("Disk error")):
            alarms = db.get_pending_alarms(self.chat_id)
            self.assertEqual(alarms, [])

    def test_03_db_exception_during_mark_alarm_fired_returns_false(self):
        """3. Database exception during mark_alarm_fired returns False safely."""
        with patch("db.session.get_db", side_effect=Exception("Write failure")):
            res = db.mark_alarm_fired("non_existent_id")
            self.assertFalse(res)

    def test_04_db_exception_during_list_and_delete_alarms_returns_safe_defaults(self):
        """4. Database exceptions during list_alarms and delete_alarm return safe defaults."""
        with patch("db.session.get_db", side_effect=Exception("Corrupted DB")):
            self.assertEqual(db.list_alarms(self.chat_id), [])
            self.assertFalse(db.delete_alarm("alarm_123"))

    # --- 2. Telegram Send Failure & Retry Behavior Tests ---

    @patch("main.send_telegram_message", return_value=False)
    def test_05_telegram_send_failure_leaves_alarm_pending(self, mock_send):
        """5. Telegram send failure leaves alarm in pending state (fired = 0) for retry."""
        past_ts = datetime.now().timestamp() - 10
        alarm_id = db.save_alarm(self.chat_id, "Unsent alarm", past_ts)

        check_and_fire_due_alarms()

        pending = db.get_pending_alarms(self.chat_id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], alarm_id)
        self.assertEqual(pending[0]["fired"], 0)

    @patch("main.send_telegram_message", return_value=True)
    def test_06_telegram_send_success_marks_alarm_fired(self, mock_send):
        """6. Successful Telegram send marks alarm as fired (fired = 1)."""
        past_ts = datetime.now().timestamp() - 10
        alarm_id = db.save_alarm(self.chat_id, "Sent alarm", past_ts)

        check_and_fire_due_alarms()

        pending = db.get_pending_alarms(self.chat_id)
        self.assertEqual(len(pending), 0)

    # --- 3. Background Checker Isolation & Resilience Tests ---

    @patch("main.send_telegram_message")
    def test_07_one_failing_alarm_does_not_prevent_other_due_alarms(self, mock_send):
        """7. Exception when firing alarm A does not prevent alarm B from firing."""
        past_ts = datetime.now().timestamp() - 10
        alarm_a = db.save_alarm(self.chat_id, "Broken alarm A", past_ts)
        alarm_b = db.save_alarm(self.chat_id, "Healthy alarm B", past_ts)

        # Mock send_telegram_message to throw exception on alarm A, but succeed on alarm B
        def side_effect_send(msg, chat_id=None):
            if "Broken alarm A" in msg:
                raise Exception("Simulated network timeout")
            return True

        mock_send.side_effect = side_effect_send

        check_and_fire_due_alarms()

        pending = db.get_pending_alarms(self.chat_id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], alarm_a)

    @patch("main.check_and_fire_due_alarms", side_effect=[Exception("Fatal cycle crash"), None])
    @patch("time.sleep")
    def test_08_checker_loop_survives_unexpected_cycle_exception(self, mock_sleep, mock_check):
        """8. Unexpected cycle exception inside alarm_checker_loop does not terminate thread loop."""
        # Force sleep to raise an exception on second call to exit infinite loop cleanly
        mock_sleep.side_effect = [None, RuntimeError("Exit loop test")]

        with self.assertRaises(RuntimeError):
            alarm_checker_loop()

        self.assertEqual(mock_check.call_count, 2)

    # --- 4. Calendar Error Handling Tests ---

    def test_09_calendar_api_failure_returns_sanitized_error(self):
        """9. Google Calendar API failure returns safe sanitized error message."""
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", side_effect=Exception("OAuth 401 Unauthorized token expired")):
            res = process_calendar_relative_reminder("Remind me 30 minutes before my RAG meeting.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["message"], "I couldn't access Google Calendar right now.")
            self.assertNotIn("OAuth", res["message"])

    def test_10_malformed_calendar_response_handled_safely(self):
        """10. Malformed non-list or non-dict calendar items handled safely without crash."""
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        malformed_events = [None, "invalid string event", {"id": "1"}]  # missing title and start
        with patch("tools.calendar.list_upcoming_events", return_value=malformed_events):
            res = process_calendar_relative_reminder("Remind me 30 minutes before my RAG meeting.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "error")

    def test_11_all_day_and_missing_start_time_handled_safely(self):
        """11. All-day events and events missing start dateTime return clean explanation."""
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        mock_events = [{"id": "1", "title": "RAG All-Day Event", "start": "2026-09-11"}]
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 30 minutes before my RAG All-Day Event.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "error")
            self.assertIn("all-day event", res["message"])

    # --- 5. Confirmation Safety & Edge Cases Tests ---

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_12_duplicate_confirm_click_handled_safely(self, mock_answer, mock_send):
        """12. Rapid duplicate click on Confirm processes first click and safely ignores second."""
        action_id = "act_phase48_12"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Duplicate confirm test",
            "fire_at": "2026-09-11T14:30:00"
        })

        cq = {
            "id": "cq_48_12",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        }
        handle_callback_query(cq)
        handle_callback_query(cq)

        pending = db.get_pending_alarms(self.chat_id)
        self.assertEqual(len(pending), 1)

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_13_unknown_and_expired_action_ids_handled_safely(self, mock_answer, mock_send):
        """13. Unknown or expired action IDs handled without crash or duplicate alarm."""
        cq = {
            "id": "cq_48_13",
            "data": "confirm_alarm:non_existent_action_999",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        }
        handle_callback_query(cq)
        mock_send.assert_called()
        sent_text = mock_send.call_args[0][0].lower()
        self.assertTrue("expired" in sent_text or "no longer available" in sent_text)

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_14_chat_isolation_enforced_on_confirmation(self, mock_answer, mock_send):
        """14. Chat B cannot confirm or cancel Chat A's pending alarm action."""
        action_id = "act_phase48_14"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "User A Private Reminder",
            "fire_at": "2026-09-11T14:30:00"
        })

        other_chat_id = "attacker_chat_777"
        cq = {
            "id": "cq_48_14",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": other_chat_id},
            "message": {"chat": {"id": other_chat_id}}
        }
        handle_callback_query(cq)

        pending = db.get_pending_alarms(self.chat_id)
        self.assertEqual(len(pending), 0)

    # --- 6. Malformed Reminder Data Tests ---

    @patch("main.send_telegram_message")
    def test_15_malformed_set_alarm_args_handled_safely(self, mock_send):
        """15. Empty message or invalid fire_at handled safely without crash."""
        _present_alarm_confirmation(self.chat_id, {"message": "", "fire_at": ""})
        mock_send.assert_called_once()
        self.assertIn("Please specify message and time", mock_send.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
