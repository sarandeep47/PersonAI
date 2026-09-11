import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

import db.session as db
from agent.schemas import ToolCall
from main import (
    handle_message,
    handle_callback_query,
    _present_alarm_confirmation,
    _format_alarm_datetime,
)


class TestPhase45AlarmConfirmation(unittest.TestCase):
    """Focused unit tests for Phase 4.5 Telegram Confirmation & Alarm Persistence."""

    def setUp(self):
        self.chat_id = "test_user_alarm_45"
        db.init_db()
        # Clean up database state for test chat_id before each test
        conn = db.get_db()
        with conn:
            conn.execute("DELETE FROM alarms WHERE chat_id = ?", (self.chat_id,))
            conn.execute("DELETE FROM pending_actions WHERE chat_id = ?", (self.chat_id,))

    def tearDown(self):
        conn = db.get_db()
        with conn:
            conn.execute("DELETE FROM alarms WHERE chat_id = ?", (self.chat_id,))
            conn.execute("DELETE FROM pending_actions WHERE chat_id = ?", (self.chat_id,))

    # --- Presentation Tests (1 to 5) ---

    @patch("main.send_telegram_message")
    def test_01_set_alarm_produces_telegram_confirmation(self, mock_send):
        """1. set_alarm tool call presents Telegram confirmation."""
        args = {"message": "Call Priya", "fire_at": "2026-09-11T09:00:00"}
        _present_alarm_confirmation(self.chat_id, args)

        mock_send.assert_called_once()
        sent_msg = mock_send.call_args[0][0]
        self.assertIn("⏰ *Reminder*", sent_msg)

    @patch("main.send_telegram_message")
    def test_02_confirmation_displays_reminder_message(self, mock_send):
        """2. Confirmation displays the reminder message."""
        args = {"message": "Review RAG project", "fire_at": "2026-09-11T14:30:00"}
        _present_alarm_confirmation(self.chat_id, args)

        sent_msg = mock_send.call_args[0][0]
        self.assertIn("Review RAG project", sent_msg)

    @patch("main.send_telegram_message")
    def test_03_confirmation_displays_correct_date(self, mock_send):
        """3. Confirmation displays the formatted date."""
        args = {"message": "Meeting", "fire_at": "2026-09-11T09:00:00"}
        _present_alarm_confirmation(self.chat_id, args)

        sent_msg = mock_send.call_args[0][0]
        self.assertIn("Friday, September 11, 2026", sent_msg)

    @patch("main.send_telegram_message")
    def test_04_confirmation_displays_correct_time(self, mock_send):
        """4. Confirmation displays the formatted 12-hour time."""
        args = {"message": "Meeting", "fire_at": "2026-09-11T14:30:00"}
        _present_alarm_confirmation(self.chat_id, args)

        sent_msg = mock_send.call_args[0][0]
        self.assertIn("2:30 PM", sent_msg)

    @patch("main.send_telegram_message")
    def test_05_confirmation_contains_confirm_and_cancel_buttons(self, mock_send):
        """5. Confirmation reply_markup contains Confirm and Cancel buttons."""
        args = {"message": "Check deployment", "fire_at": "2026-09-10T20:30:00"}
        _present_alarm_confirmation(self.chat_id, args)

        reply_markup = mock_send.call_args[1].get("reply_markup") or mock_send.call_args[0][1]
        buttons = reply_markup["inline_keyboard"][0]
        self.assertEqual(len(buttons), 3)
        self.assertIn("Confirm", buttons[0]["text"])
        self.assertIn("confirm_alarm:", buttons[0]["callback_data"])
        self.assertIn("Edit", buttons[1]["text"])
        self.assertIn("edit_alarm:", buttons[1]["callback_data"])
        self.assertIn("Cancel", buttons[2]["text"])
        self.assertIn("cancel_alarm:", buttons[2]["callback_data"])

    # --- Confirm Tests (6 to 10) ---

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_06_confirm_creates_exactly_one_alarm(self, mock_answer, mock_send):
        """6. Confirm callback creates exactly one alarm in SQLite."""
        action_id = "test_act_06"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Call Priya",
            "fire_at": "2026-09-11T09:00:00"
        })

        cq = {
            "id": "cq_06",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        }
        handle_callback_query(cq)

        pending = db.get_pending_alarms(self.chat_id)
        self.assertEqual(len(pending), 1)

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_07_confirm_stores_correct_chat_id(self, mock_answer, mock_send):
        """7. Confirm callback stores the correct chat_id."""
        action_id = "test_act_07"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Call Priya",
            "fire_at": "2026-09-11T09:00:00"
        })

        handle_callback_query({
            "id": "cq_07",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        })

        alarm = db.get_pending_alarms(self.chat_id)[0]
        self.assertEqual(alarm["chat_id"], self.chat_id)

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_08_confirm_stores_correct_message(self, mock_answer, mock_send):
        """8. Confirm callback stores the correct message text."""
        action_id = "test_act_08"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Review RAG project",
            "fire_at": "2026-09-11T14:30:00"
        })

        handle_callback_query({
            "id": "cq_08",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        })

        alarm = db.get_pending_alarms(self.chat_id)[0]
        self.assertEqual(alarm["message"], "Review RAG project")

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_09_confirm_stores_correct_fire_at(self, mock_answer, mock_send):
        """9. Confirm callback stores correct fire_at Unix timestamp."""
        fire_iso = "2026-09-11T09:00:00"
        expected_ts = datetime.fromisoformat(fire_iso).timestamp()

        action_id = "test_act_09"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Test alarm",
            "fire_at": fire_iso
        })

        handle_callback_query({
            "id": "cq_09",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        })

        alarm = db.get_pending_alarms(self.chat_id)[0]
        self.assertAlmostEqual(alarm["fire_at"], expected_ts, places=2)

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_10_pending_action_consumed_after_confirmation(self, mock_answer, mock_send):
        """10. Pending action is consumed/deleted after confirmation."""
        action_id = "test_act_10"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Test alarm",
            "fire_at": "2026-09-11T09:00:00"
        })

        handle_callback_query({
            "id": "cq_10",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        })

        # Pending action should no longer exist
        self.assertIsNone(db.get_pending_action(action_id))

    # --- Cancel Tests (11 to 13) ---

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_11_cancel_does_not_create_alarm(self, mock_answer, mock_send):
        """11. Cancel callback does NOT create an alarm."""
        action_id = "test_act_11"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Cancel me",
            "fire_at": "2026-09-11T09:00:00"
        })

        handle_callback_query({
            "id": "cq_11",
            "data": f"cancel_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        })

        pending = db.get_pending_alarms(self.chat_id)
        self.assertEqual(len(pending), 0)

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_12_cancel_consumes_pending_action(self, mock_answer, mock_send):
        """12. Cancel callback consumes/deletes the pending action."""
        action_id = "test_act_12"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Cancel me",
            "fire_at": "2026-09-11T09:00:00"
        })

        handle_callback_query({
            "id": "cq_12",
            "data": f"cancel_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        })

        self.assertIsNone(db.get_pending_action(action_id))

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_13_cancellation_updates_telegram_ui(self, mock_answer, mock_send):
        """13. Cancellation sends cancellation feedback message to Telegram."""
        action_id = "test_act_13"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Cancel me",
            "fire_at": "2026-09-11T09:00:00"
        })

        handle_callback_query({
            "id": "cq_13",
            "data": f"cancel_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        })

        mock_send.assert_called()
        sent_text = mock_send.call_args[0][0]
        self.assertIn("cancelled", sent_text.lower())

    # --- Safety Tests (14 to 18) ---

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_14_double_confirm_cannot_create_duplicate_alarms(self, mock_answer, mock_send):
        """14. Double clicking Confirm creates only ONE alarm."""
        action_id = "test_act_14"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Double click test",
            "fire_at": "2026-09-11T09:00:00"
        })

        cq = {
            "id": "cq_14",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        }
        # First click
        handle_callback_query(cq)
        # Second click
        handle_callback_query(cq)

        pending = db.get_pending_alarms(self.chat_id)
        self.assertEqual(len(pending), 1)

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_15_double_cancel_is_safe(self, mock_answer, mock_send):
        """15. Double clicking Cancel is safe and does not error."""
        action_id = "test_act_15"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Double cancel test",
            "fire_at": "2026-09-11T09:00:00"
        })

        cq = {
            "id": "cq_15",
            "data": f"cancel_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        }
        handle_callback_query(cq)
        handle_callback_query(cq)  # Second click

        self.assertEqual(len(db.get_pending_alarms(self.chat_id)), 0)

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_16_wrong_chat_cannot_confirm_another_chats_alarm(self, mock_answer, mock_send):
        """16. Chat B cannot confirm Chat A's pending alarm."""
        action_id = "test_act_16"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Private alarm",
            "fire_at": "2026-09-11T09:00:00"
        })

        other_chat_id = "malicious_user_999"
        cq = {
            "id": "cq_16",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": other_chat_id},
            "message": {"chat": {"id": other_chat_id}}
        }
        handle_callback_query(cq)

        # Alarm must NOT have been saved
        self.assertEqual(len(db.get_pending_alarms(self.chat_id)), 0)

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_17_wrong_action_type_rejected(self, mock_answer, mock_send):
        """17. Callback with wrong action_type is rejected."""
        action_id = "test_act_17"
        db.save_pending_action(action_id, self.chat_id, "confirm_send", {
            "to": "test@example.com", "subject": "hi", "body": "hi"
        })

        cq = {
            "id": "cq_17",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        }
        handle_callback_query(cq)

        self.assertEqual(len(db.get_pending_alarms(self.chat_id)), 0)

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_18_missing_or_expired_pending_action_handled_safely(self, mock_answer, mock_send):
        """18. Non-existent or expired callback action handled safely."""
        cq = {
            "id": "cq_18",
            "data": "confirm_alarm:non_existent_id",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        }
        handle_callback_query(cq)

        mock_send.assert_called()
        sent_text = mock_send.call_args[0][0]
        self.assertTrue("expired" in sent_text.lower() or "no longer available" in sent_text.lower())

    # --- Error Handling Tests (19 and 20) ---

    @patch("db.session.save_alarm", side_effect=Exception("Database locked error"))
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_19_save_alarm_failure_does_not_crash(self, mock_answer, mock_send, mock_save):
        """19. save_alarm failure handles exception without crashing bot."""
        action_id = "test_act_19"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Crash test",
            "fire_at": "2026-09-11T09:00:00"
        })

        cq = {
            "id": "cq_19",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        }
        # Should not raise exception
        handle_callback_query(cq)

        mock_send.assert_called()
        sent_text = mock_send.call_args[0][0]
        self.assertIn("went wrong", sent_text)

    @patch("db.session.save_alarm", side_effect=Exception("SECRET_DB_PATH_C:/private/keys"))
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_20_internal_error_details_not_leaked(self, mock_answer, mock_send, mock_save):
        """20. Internal stack traces or paths are not leaked to Telegram user."""
        action_id = "test_act_20"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Secret test",
            "fire_at": "2026-09-11T09:00:00"
        })

        cq = {
            "id": "cq_20",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        }
        handle_callback_query(cq)

        sent_text = mock_send.call_args[0][0]
        self.assertNotIn("SECRET_DB_PATH", sent_text)
        self.assertNotIn("C:/private/keys", sent_text)

    # --- Scope Tests (21 and 22) ---

    @patch("db.session.save_alarm")
    @patch("main.send_telegram_message")
    def test_21_initial_tool_routing_does_not_call_save_alarm(self, mock_send, mock_save):
        """21. Initial presentation of set_alarm confirmation does NOT call save_alarm()."""
        args = {"message": "Test alarm", "fire_at": "2026-09-11T09:00:00"}
        _present_alarm_confirmation(self.chat_id, args)

        mock_save.assert_not_called()

    @patch("db.session.save_alarm")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_22_cancel_does_not_call_save_alarm(self, mock_answer, mock_send, mock_save):
        """22. Cancel callback does NOT call save_alarm()."""
        action_id = "test_act_22"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Test alarm",
            "fire_at": "2026-09-11T09:00:00"
        })

        handle_callback_query({
            "id": "cq_22",
            "data": f"cancel_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        })

        mock_save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
