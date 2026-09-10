# test_phase46_alarm_checker.py
import unittest
import time
import os
import tempfile
import threading
from unittest.mock import patch, MagicMock

import db.session as db
import main
from main import check_and_fire_due_alarms, alarm_checker_loop, ALARM_CHECK_INTERVAL_SECONDS


class TestPhase46AlarmChecker(unittest.TestCase):
    def setUp(self):
        # Use an isolated temporary SQLite database for test runs
        self.tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp_db.close()
        self.db_path = self.tmp_db.name
        self.patcher = patch("db.session.DB_PATH", self.db_path)
        self.patcher.start()
        db.init_db()

        self.chat_id = "123456789"
        self.now_ts = int(time.time())

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except OSError:
                pass

    # --- Basic Firing & Delivery Tests (1-5) ---

    @patch("main.send_telegram_message")
    def test_01_due_alarm_detected(self, mock_send):
        """1. Due pending alarm is detected."""
        alarm_id = db.save_alarm(self.chat_id, "Check status", self.now_ts - 10)
        check_and_fire_due_alarms()
        mock_send.assert_called_once()

    @patch("main.send_telegram_message")
    def test_02_telegram_reminder_sent(self, mock_send):
        """2. Telegram reminder is sent when alarm is due."""
        db.save_alarm(self.chat_id, "Call Priya", self.now_ts - 5)
        check_and_fire_due_alarms()
        mock_send.assert_called_once()

    @patch("main.send_telegram_message")
    def test_03_correct_chat_id_used(self, mock_send):
        """3. Correct chat_id is used for sending the Telegram reminder."""
        specific_chat = "99887766"
        db.save_alarm(specific_chat, "Call Priya", self.now_ts - 5)
        check_and_fire_due_alarms()
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args[1].get("chat_id"), specific_chat)

    @patch("main.send_telegram_message")
    def test_04_correct_reminder_message_format(self, mock_send):
        """4. Correct reminder message text is formatted (⏰ Reminder: {message})."""
        msg_text = "Team sync with Priya starts in 30 minutes!"
        db.save_alarm(self.chat_id, msg_text, self.now_ts - 5)
        check_and_fire_due_alarms()
        sent_text = mock_send.call_args[0][0]
        self.assertEqual(sent_text, f"⏰ Reminder: {msg_text}")

    @patch("main.send_telegram_message")
    def test_05_alarm_marked_fired_after_delivery(self, mock_send):
        """5. Alarm is marked fired in SQLite after successful delivery."""
        alarm_id = db.save_alarm(self.chat_id, "Deploy update", self.now_ts - 5)
        check_and_fire_due_alarms()
        pending = db.get_pending_alarms()
        self.assertEqual(len(pending), 0)

    # --- Timing Tests (6-8) ---

    @patch("main.send_telegram_message")
    def test_06_future_alarm_not_fired(self, mock_send):
        """6. Alarm scheduled in the future is not fired."""
        db.save_alarm(self.chat_id, "Future meeting", self.now_ts + 300)
        check_and_fire_due_alarms()
        mock_send.assert_not_called()
        pending = db.get_pending_alarms()
        self.assertEqual(len(pending), 1)

    @patch("main.send_telegram_message")
    def test_07_alarm_at_current_time_fired(self, mock_send):
        """7. Alarm exactly at the current timestamp is fired."""
        db.save_alarm(self.chat_id, "Exact time alarm", self.now_ts)
        check_and_fire_due_alarms()
        mock_send.assert_called_once()
        pending = db.get_pending_alarms()
        self.assertEqual(len(pending), 0)

    @patch("main.send_telegram_message")
    def test_08_overdue_alarm_fired(self, mock_send):
        """8. Alarm past its fire_at timestamp (overdue) is fired."""
        db.save_alarm(self.chat_id, "Overdue task", self.now_ts - 3600)
        check_and_fire_due_alarms()
        mock_send.assert_called_once()
        pending = db.get_pending_alarms()
        self.assertEqual(len(pending), 0)

    # --- Multiple Alarms Tests (9-10) ---

    @patch("main.send_telegram_message")
    def test_09_multiple_due_alarms_processed(self, mock_send):
        """9. Multiple due alarms are all processed in a single cycle."""
        db.save_alarm(self.chat_id, "Alarm 1", self.now_ts - 10)
        db.save_alarm(self.chat_id, "Alarm 2", self.now_ts - 5)
        db.save_alarm(self.chat_id, "Alarm 3", self.now_ts)
        check_and_fire_due_alarms()
        self.assertEqual(mock_send.call_count, 3)
        self.assertEqual(len(db.get_pending_alarms()), 0)

    @patch("main.send_telegram_message")
    def test_10_future_alarms_remain_pending(self, mock_send):
        """10. Future alarms remain pending while due alarms fire."""
        db.save_alarm(self.chat_id, "Due Alarm", self.now_ts - 5)
        db.save_alarm(self.chat_id, "Future Alarm", self.now_ts + 600)
        check_and_fire_due_alarms()
        mock_send.assert_called_once()
        pending = db.get_pending_alarms()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["message"], "Future Alarm")

    # --- Duplicate Protection Tests (11-12) ---

    @patch("main.send_telegram_message")
    def test_11_fired_alarm_not_sent_again(self, mock_send):
        """11. An alarm marked fired is not returned by get_pending_alarms or delivered."""
        alarm_id = db.save_alarm(self.chat_id, "Already fired", self.now_ts - 10)
        db.mark_alarm_fired(alarm_id)
        check_and_fire_due_alarms()
        mock_send.assert_not_called()

    @patch("main.send_telegram_message")
    def test_12_second_polling_cycle_does_not_resend(self, mock_send):
        """12. Subsequent polling cycle does not resend an alarm fired in the first cycle."""
        db.save_alarm(self.chat_id, "Single fire alarm", self.now_ts - 5)
        check_and_fire_due_alarms()
        self.assertEqual(mock_send.call_count, 1)

        # Second cycle
        check_and_fire_due_alarms()
        self.assertEqual(mock_send.call_count, 1)

    # --- Failure Handling Tests (13-15) ---

    @patch("main.send_telegram_message", side_effect=Exception("Telegram connection timeout"))
    def test_13_telegram_failure_does_not_mark_fired(self, mock_send):
        """13. If Telegram delivery fails, the alarm is NOT marked fired."""
        db.save_alarm(self.chat_id, "Failing delivery", self.now_ts - 5)
        check_and_fire_due_alarms()
        pending = db.get_pending_alarms()
        self.assertEqual(len(pending), 1)

    @patch("main.send_telegram_message")
    def test_14_failed_alarm_does_not_block_others(self, mock_send):
        """14. A failure processing one alarm does not prevent other due alarms from firing."""
        id1 = db.save_alarm(self.chat_id, "Fail alarm", self.now_ts - 10)
        id2 = db.save_alarm(self.chat_id, "Success alarm", self.now_ts - 5)

        def mock_send_side_effect(msg, chat_id=None):
            if "Fail alarm" in msg:
                raise Exception("Network error for Fail alarm")
            return None

        mock_send.side_effect = mock_send_side_effect
        check_and_fire_due_alarms()

        # Success alarm should be marked fired, Fail alarm remains pending
        pending = db.get_pending_alarms()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], id1)

    @patch("db.session.get_pending_alarms", side_effect=Exception("Database lock error"))
    def test_15_unexpected_checker_error_does_not_crash_loop(self, mock_db):
        """15. Unexpected errors during check_and_fire_due_alarms do not crash or propagate."""
        try:
            check_and_fire_due_alarms()
        except Exception as e:
            self.fail(f"check_and_fire_due_alarms raised an exception: {e}")

    # --- Startup & Thread Behavior Tests (16-18) ---

    @patch("main.send_telegram_message")
    def test_16_startup_overdue_alarms_processed(self, mock_send):
        """16. Existing overdue alarms stored in SQLite prior to loop start are processed."""
        # Pre-populate database with overdue alarm
        db.save_alarm(self.chat_id, "Pre-existing overdue alarm", self.now_ts - 1000)

        # Simulate startup check
        check_and_fire_due_alarms()
        mock_send.assert_called_once()
        self.assertEqual(len(db.get_pending_alarms()), 0)

    def test_17_checker_starts_as_daemon_thread(self):
        """17. Checker thread configured as a daemon thread."""
        t = threading.Thread(target=alarm_checker_loop, daemon=True)
        self.assertTrue(t.daemon)

    @patch("main.listen_for_telegram_messages", side_effect=KeyboardInterrupt)
    @patch("main.send_telegram_message")
    def test_18_single_checker_thread_started(self, mock_send, mock_listen):
        """18. Normal application main() starts exactly one alarm checker thread."""
        initial_threads = [t for t in threading.enumerate() if t.name == "AlarmCheckerThread"]
        self.assertEqual(len(initial_threads), 0)

        try:
            main.main()
        except KeyboardInterrupt:
            pass

        checker_threads = [t for t in threading.enumerate() if t.name == "AlarmCheckerThread"]
        self.assertEqual(len(checker_threads), 1)
        self.assertTrue(checker_threads[0].daemon)


if __name__ == "__main__":
    unittest.main()
