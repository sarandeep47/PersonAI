import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

import db.session as db
from agent.core import (
    parse_calendar_relative_intent,
    process_calendar_relative_reminder,
    validate_tool_call,
    call_agent,
)
from agent.schemas import ToolCall
from main import handle_callback_query, _present_alarm_confirmation


class TestPhase47CalendarRelativeAlarm(unittest.TestCase):
    """Focused unit tests for Phase 4.7 Calendar-Relative Reminders."""

    def setUp(self):
        self.chat_id = "test_user_phase47"
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

    # --- Intent / Parsing Tests (1 to 3) ---

    def test_01_parse_intent_30_minutes_before_rag_meeting(self):
        """1. Parse 'Remind me 30 minutes before my RAG meeting.'"""
        res = parse_calendar_relative_intent("Remind me 30 minutes before my RAG meeting.")
        self.assertIsNotNone(res)
        self.assertEqual(res["offset_minutes"], 30)
        self.assertEqual(res["event_ref"], "RAG meeting")

    def test_02_parse_intent_1_hour_before_team_meeting(self):
        """2. Parse 'Remind me 1 hour before my team meeting.'"""
        res = parse_calendar_relative_intent("Remind me 1 hour before my team meeting.")
        self.assertIsNotNone(res)
        self.assertEqual(res["offset_minutes"], 60)
        self.assertEqual(res["event_ref"], "team meeting")

    def test_03_parse_intent_15_mins_before_meeting_with_priya(self):
        """3. Parse 'Remind me 15 mins before my meeting with Priya.'"""
        res = parse_calendar_relative_intent("Remind me 15 mins before my meeting with Priya.")
        self.assertIsNotNone(res)
        self.assertEqual(res["offset_minutes"], 15)
        self.assertEqual(res["event_ref"], "meeting with Priya")

    # --- Event Matching Tests (4 to 8) ---

    def test_04_single_matching_event_selected(self):
        """4. Correct event selected when exactly one event matches."""
        mock_events = [
            {"id": "1", "title": "RAG Project Sync", "start": "2026-09-11T15:00:00+05:30", "end": "2026-09-11T16:00:00+05:30"},
            {"id": "2", "title": "Budget Review", "start": "2026-09-11T17:00:00+05:30", "end": "2026-09-11T18:00:00+05:30"},
        ]
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 30 minutes before my RAG meeting.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["selected_event"]["id"], "1")

    def test_05_title_matching_is_case_insensitive(self):
        """5. Title matching works case-insensitively."""
        mock_events = [
            {"id": "1", "title": "rag sync meeting", "start": "2026-09-11T15:00:00+05:30", "end": "2026-09-11T16:00:00+05:30"},
        ]
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 30 minutes before my RAG MEETING.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["selected_event"]["id"], "1")

    def test_06_no_matching_event_returns_clean_error(self):
        """6. No matching event returns clean error and no alarm."""
        mock_events = [
            {"id": "1", "title": "Budget Review", "start": "2026-09-11T15:00:00+05:30", "end": "2026-09-11T16:00:00+05:30"},
        ]
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 30 minutes before my Python meeting.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["message"], "I couldn't find a matching calendar event.")

    def test_07_multiple_ambiguous_events_asks_clarification(self):
        """7. Multiple ambiguous events returns clarification error and no alarm."""
        mock_events = [
            {"id": "1", "title": "Team Standup", "start": "2026-09-11T10:00:00+05:30", "end": "2026-09-11T10:30:00+05:30"},
            {"id": "2", "title": "Team Retrospective", "start": "2026-09-11T16:00:00+05:30", "end": "2026-09-11T17:00:00+05:30"},
        ]
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 30 minutes before my team meeting.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "error")
            self.assertIn("multiple matching meetings", res["message"])

    def test_08_attendee_matching_supported(self):
        """8. Event matching matches attendee email/name where supported."""
        mock_events = [
            {"id": "1", "title": "Project Sync", "attendees": ["priya@example.com"], "start": "2026-09-11T15:00:00+05:30"},
            {"id": "2", "title": "Client Call", "attendees": ["john@example.com"], "start": "2026-09-11T17:00:00+05:30"},
        ]
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 15 mins before my meeting with Priya.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["selected_event"]["id"], "1")

    # --- Offset Calculation Tests (9 to 12) ---

    def test_09_offset_calculation_30_minutes(self):
        """9. 30-minute offset produces correct alarm time."""
        mock_events = [
            {"id": "1", "title": "RAG Sync", "start": "2026-09-11T15:00:00+05:30"},
        ]
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 30 minutes before my RAG Sync.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["fire_at"], "2026-09-11T14:30:00")

    def test_10_offset_calculation_1_hour(self):
        """10. 1-hour offset produces correct alarm time."""
        mock_events = [
            {"id": "1", "title": "Team Sync", "start": "2026-09-11T15:00:00+05:30"},
        ]
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 1 hour before my Team Sync.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["fire_at"], "2026-09-11T14:00:00")

    def test_11_offset_calculation_90_minutes(self):
        """11. 90-minute offset produces correct alarm time."""
        mock_events = [
            {"id": "1", "title": "RAG Meeting", "start": "2026-09-11T15:00:00+05:30"},
        ]
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 90 minutes before my RAG Meeting.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["fire_at"], "2026-09-11T13:30:00")

    def test_12_offset_calculation_day_hour_boundary_crossing(self):
        """12. Offset calculation correctly crosses hour and day boundaries."""
        # Event at 00:15 on Sept 12; offset 30 mins -> alarm at 23:45 on Sept 11
        mock_events = [
            {"id": "1", "title": "Midnight Release", "start": "2026-09-12T00:15:00+05:30"},
        ]
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 30 minutes before my Midnight Release.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["fire_at"], "2026-09-11T23:45:00")

    # --- Event Validity Tests (13 to 15) ---

    def test_13_past_event_does_not_create_alarm(self):
        """13. Past event returns clean explanation without creating an alarm."""
        mock_events = [
            {"id": "1", "title": "Morning Sync", "start": "2026-09-11T08:00:00+05:30"},
        ]
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 30 minutes before my Morning Sync.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["message"], "That meeting has already started or ended.")

    def test_14_all_day_event_does_not_create_alarm(self):
        """14. All-day event returns clean explanation without creating an alarm."""
        mock_events = [
            {"id": "1", "title": "Company Holiday", "start": "2026-09-11"},
        ]
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 30 minutes before my Company Holiday.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["message"], "I can't set a relative reminder for an all-day event without a specific start time.")

    def test_15_future_timed_event_creates_alarm_candidate(self):
        """15. Future timed event creates correct alarm candidate."""
        mock_events = [
            {"id": "1", "title": "RAG Sync", "start": "2026-09-11T15:00:00+05:30"},
        ]
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        with patch("tools.calendar.list_upcoming_events", return_value=mock_events):
            res = process_calendar_relative_reminder("Remind me 30 minutes before my RAG Sync.", reference_datetime=ref_dt)
            self.assertEqual(res["status"], "success")
            self.assertIn("RAG Sync starts in 30 minutes", res["message"])
            self.assertEqual(res["fire_at"], "2026-09-11T14:30:00")

    # --- Confirmation Safety Tests (16 to 20) ---

    @patch("main.send_telegram_message")
    def test_16_calendar_relative_reminder_uses_existing_confirmation(self, mock_send):
        """16. Calendar-relative reminder candidate routes through existing confirmation UI."""
        args = {"message": "RAG project meeting starts in 30 minutes.", "fire_at": "2026-09-11T14:30:00"}
        _present_alarm_confirmation(self.chat_id, args)

        mock_send.assert_called_once()
        sent_msg = mock_send.call_args[0][0]
        self.assertIn("⏰ *Reminder*", sent_msg)
        self.assertIn("RAG project meeting starts in 30 minutes.", sent_msg)

    @patch("db.session.save_alarm")
    @patch("main.send_telegram_message")
    def test_17_save_alarm_not_called_before_confirmation(self, mock_send, mock_save):
        """17. save_alarm() is NOT called before confirmation."""
        args = {"message": "RAG project meeting starts in 30 minutes.", "fire_at": "2026-09-11T14:30:00"}
        _present_alarm_confirmation(self.chat_id, args)

        mock_save.assert_not_called()

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_18_confirm_creates_exactly_one_alarm(self, mock_answer, mock_send):
        """18. Confirm callback creates exactly one alarm in SQLite."""
        action_id = "act_phase47_18"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "RAG project meeting starts in 30 minutes.",
            "fire_at": "2026-09-11T14:30:00"
        })

        cq = {
            "id": "cq_47_18",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        }
        handle_callback_query(cq)

        pending = db.get_pending_alarms(self.chat_id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["message"], "RAG project meeting starts in 30 minutes.")

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_19_cancel_creates_no_alarm(self, mock_answer, mock_send):
        """19. Cancel callback creates no alarm."""
        action_id = "act_phase47_19"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "Cancel relative reminder",
            "fire_at": "2026-09-11T14:30:00"
        })

        cq = {
            "id": "cq_47_19",
            "data": f"cancel_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        }
        handle_callback_query(cq)

        pending = db.get_pending_alarms(self.chat_id)
        self.assertEqual(len(pending), 0)

    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_20_double_confirmation_cannot_create_duplicates(self, mock_answer, mock_send):
        """20. Double clicking confirm button cannot create duplicate alarms."""
        action_id = "act_phase47_20"
        db.save_pending_action(action_id, self.chat_id, "confirm_set_alarm", {
            "message": "RAG meeting starts in 30 minutes.",
            "fire_at": "2026-09-11T14:30:00"
        })

        cq = {
            "id": "cq_47_20",
            "data": f"confirm_alarm:{action_id}",
            "from": {"id": self.chat_id},
            "message": {"chat": {"id": self.chat_id}}
        }
        handle_callback_query(cq)
        handle_callback_query(cq)  # Second click

        pending = db.get_pending_alarms(self.chat_id)
        self.assertEqual(len(pending), 1)

    # --- OAuth Integration Boundary Tests (21 to 23) ---

    def test_21_reuses_existing_calendar_service_path(self):
        """21. Existing get_calendar_service path from tools.google_auth is reused."""
        import tools.google_auth
        self.assertTrue(hasattr(tools.google_auth, "get_calendar_service"))

    def test_22_no_api_key_introduced(self):
        """22. Verifies no developerKey or raw API keys are introduced."""
        import inspect
        import tools.calendar
        src = inspect.getsource(tools.calendar)
        self.assertNotIn("developerKey", src)
        self.assertNotIn("api_key", src.lower())

    def test_23_no_second_oauth_flow(self):
        """23. Reuses existing credentials/token setup without adding a second flow."""
        import tools.google_auth
        import inspect
        src = inspect.getsource(tools.google_auth)
        self.assertIn("CREDENTIALS_FILE", src)
        self.assertIn("TOKEN_FILE", src)

    # --- Existing Reminders Regression Tests (24 and 25) ---

    def test_24_normal_in_30_minutes_reminder_works(self):
        """24. Normal 'Remind me in 30 minutes.' continues working as set_alarm tool call."""
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        tool_call = ToolCall(tool="set_alarm", args={"message": "check deployment", "fire_at": "in 30 minutes"})
        validated = validate_tool_call(tool_call, "Remind me in 30 minutes to check deployment.", reference_datetime=ref_dt)
        self.assertEqual(validated.tool, "set_alarm")
        self.assertEqual(validated.args["fire_at"], "2026-09-11T09:30:00")

    def test_25_normal_tomorrow_at_9am_reminder_works(self):
        """25. Normal 'Remind me tomorrow at 9 AM.' continues working as set_alarm tool call."""
        ref_dt = datetime.fromisoformat("2026-09-11T09:00:00+05:30")
        tool_call = ToolCall(tool="set_alarm", args={"message": "call Priya", "fire_at": "tomorrow at 9 AM"})
        validated = validate_tool_call(tool_call, "Remind me tomorrow at 9 AM to call Priya.", reference_datetime=ref_dt)
        self.assertEqual(validated.tool, "set_alarm")
        self.assertEqual(validated.args["fire_at"], "2026-09-12T09:00:00")


if __name__ == "__main__":
    unittest.main()
