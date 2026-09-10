import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

import db.session as db
from agent.prompts import AGENT_SYSTEM_PROMPT
from agent.schemas import ToolCall, TaskPlan, SetAlarmArgs
from agent.core import call_agent, validate_tool_call

# Explicit reference datetime: Thursday, September 10, 2026 at 20:00:00
REF_DT = datetime(2026, 9, 10, 20, 0, 0)


class TestPhase44AlarmLLM(unittest.TestCase):
    """Focused unit tests for Phase 4.4 LLM Tool Integration for set_alarm."""

    # --- Routing Tests (1 to 5) ---

    @patch("agent.core._ollama_chat")
    def test_01_routing_remind_in_30_minutes(self, mock_ollama):
        """1. 'Remind me in 30 minutes to check deployment.' -> set_alarm."""
        mock_ollama.return_value = (
            '{"tool": "set_alarm", '
            '"args": {"message": "Check deployment", "fire_at": "in 30 minutes"}, '
            '"reasoning": "User requested reminder in 30 minutes."}'
        )
        res = call_agent("Remind me in 30 minutes to check deployment.")
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "set_alarm")

    @patch("agent.core._ollama_chat")
    def test_02_routing_remind_tomorrow_9am(self, mock_ollama):
        """2. 'Remind me tomorrow at 9 AM to call Priya.' -> set_alarm."""
        mock_ollama.return_value = (
            '{"tool": "set_alarm", '
            '"args": {"message": "Call Priya", "fire_at": "tomorrow at 9 AM"}, '
            '"reasoning": "User requested reminder tomorrow at 9 AM."}'
        )
        res = call_agent("Remind me tomorrow at 9 AM to call Priya.")
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "set_alarm")

    @patch("agent.core._ollama_chat")
    def test_03_routing_set_reminder_friday(self, mock_ollama):
        """3. 'Set a reminder for Friday at 2:30 PM to review RAG.' -> set_alarm."""
        mock_ollama.return_value = (
            '{"tool": "set_alarm", '
            '"args": {"message": "Review RAG project", "fire_at": "Friday at 2:30 PM"}, '
            '"reasoning": "User requested reminder for Friday at 2:30 PM."}'
        )
        res = call_agent("Set a reminder for Friday at 2:30 PM to review my RAG project.")
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "set_alarm")

    @patch("agent.core._ollama_chat")
    def test_04_routing_schedule_calendar_distinction(self, mock_ollama):
        """4. 'Schedule a meeting tomorrow at 9 AM.' -> schedule_calendar."""
        mock_ollama.return_value = (
            '{"tool": "schedule_calendar", '
            '"args": {"title": "Meeting", "date": "2026-09-11", "start_time": "09:00", "duration_minutes": 30}, '
            '"reasoning": "User requested to schedule a meeting."}'
        )
        res = call_agent("Schedule a meeting tomorrow at 9 AM.")
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "schedule_calendar")

    @patch("agent.core._ollama_chat")
    def test_05_routing_list_calendar_distinction(self, mock_ollama):
        """5. 'What's on my calendar tomorrow?' -> list_calendar."""
        mock_ollama.return_value = (
            '{"tool": "list_calendar", '
            '"args": {"start_datetime": "2026-09-11T00:00:00", "end_datetime": "2026-09-11T23:59:59"}, '
            '"reasoning": "User asked to view tomorrow\'s schedule."}'
        )
        res = call_agent("What's on my calendar tomorrow?")
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "list_calendar")

    # --- Datetime Resolution Tests (6 to 10) ---

    def test_06_resolution_tomorrow_at_9am(self):
        """6. 'tomorrow at 9 AM' resolves to concrete ISO datetime."""
        tc = ToolCall(
            tool="set_alarm",
            args={"message": "Call Priya", "fire_at": "tomorrow at 9 AM"},
            reasoning="Testing resolution."
        )
        res = validate_tool_call(tc, user_message="Remind me tomorrow at 9 AM to call Priya.", reference_datetime=REF_DT)
        self.assertEqual(res.tool, "set_alarm")
        self.assertEqual(res.args["fire_at"], "2026-09-11T09:00:00")

    def test_07_resolution_in_30_minutes(self):
        """7. 'in 30 minutes' resolves to concrete ISO datetime."""
        tc = ToolCall(
            tool="set_alarm",
            args={"message": "Check deployment", "fire_at": "in 30 minutes"},
            reasoning="Testing resolution."
        )
        res = validate_tool_call(tc, user_message="Remind me in 30 minutes to check deployment.", reference_datetime=REF_DT)
        self.assertEqual(res.tool, "set_alarm")
        self.assertEqual(res.args["fire_at"], "2026-09-10T20:30:00")

    def test_08_resolution_upcoming_friday(self):
        """8. 'Friday at 2:30 PM' resolves to upcoming Friday relative to Sept 10."""
        tc = ToolCall(
            tool="set_alarm",
            args={"message": "Review RAG project", "fire_at": "Friday at 2:30 PM"},
            reasoning="Testing resolution."
        )
        res = validate_tool_call(tc, user_message="Set a reminder for Friday at 2:30 PM to review my RAG project.", reference_datetime=REF_DT)
        self.assertEqual(res.tool, "set_alarm")
        self.assertEqual(res.args["fire_at"], "2026-09-11T14:30:00")

    def test_09_resolution_next_friday(self):
        """9. 'next Friday' uses following-week semantics."""
        tc = ToolCall(
            tool="set_alarm",
            args={"message": "Review RAG project", "fire_at": "next Friday at 2:30 PM"},
            reasoning="Testing resolution."
        )
        res = validate_tool_call(tc, user_message="Set a reminder for next Friday at 2:30 PM to review RAG.", reference_datetime=REF_DT)
        self.assertEqual(res.tool, "set_alarm")
        self.assertEqual(res.args["fire_at"], "2026-09-18T14:30:00")

    def test_10_unparseable_datetime_no_guessed_timestamp(self):
        """10. Unparseable/invalid datetime falls back to tool='none' without guessing a timestamp."""
        tc = ToolCall(
            tool="set_alarm",
            args={"message": "Do something", "fire_at": "unparseable-gibberish-date"},
            reasoning="Testing invalid date handling."
        )
        res = validate_tool_call(tc, user_message="Remind me sometime to do something.", reference_datetime=REF_DT)
        self.assertEqual(res.tool, "none")
        self.assertIn("Invalid arguments provided for tool set_alarm", res.args["message"])

    # --- Validation Tests (11 to 14) ---

    def test_11_valid_set_alarm_call_passes(self):
        """11. Valid set_alarm call passes SetAlarmArgs validation."""
        tc = ToolCall(
            tool="set_alarm",
            args={"message": "Buy groceries", "fire_at": "2026-09-11T10:00:00"},
            reasoning="Valid alarm."
        )
        res = validate_tool_call(tc, user_message="Remind me tomorrow at 10 AM to buy groceries", reference_datetime=REF_DT)
        self.assertEqual(res.tool, "set_alarm")
        self.assertEqual(res.args["message"], "Buy groceries")
        self.assertEqual(res.args["fire_at"], "2026-09-11T10:00:00")

    def test_12_malformed_set_alarm_args_rejected(self):
        """12. Malformed set_alarm arguments safely rejected."""
        tc = ToolCall(
            tool="set_alarm",
            args={"message": "Test", "fire_at": "invalid_date_format", "offset_minutes": "not_an_int"},
            reasoning="Testing malformed args."
        )
        res = validate_tool_call(tc, user_message="Remind me test", reference_datetime=REF_DT)
        self.assertEqual(res.tool, "none")

    def test_13_missing_message_rejected(self):
        """13. Missing or empty message is rejected."""
        tc = ToolCall(
            tool="set_alarm",
            args={"message": "   ", "fire_at": "2026-09-11T09:00:00"},
            reasoning="Testing empty message."
        )
        res = validate_tool_call(tc, user_message="Remind me", reference_datetime=REF_DT)
        self.assertEqual(res.tool, "none")

    def test_14_invalid_fire_at_rejected(self):
        """14. Invalid fire_at is rejected."""
        tc = ToolCall(
            tool="set_alarm",
            args={"message": "Call mom", "fire_at": "invalid_iso_string"},
            reasoning="Testing invalid fire_at."
        )
        res = validate_tool_call(tc, user_message="Remind me to call mom", reference_datetime=REF_DT)
        self.assertEqual(res.tool, "none")

    # --- Safety / Scope Tests (15 and 16) ---

    @patch("db.session.save_alarm")
    @patch("agent.core._ollama_chat")
    def test_15_save_alarm_not_called_during_llm_routing(self, mock_ollama, mock_save_alarm):
        """15. Verify db.session.save_alarm() is NOT called during LLM routing."""
        mock_ollama.return_value = (
            '{"tool": "set_alarm", '
            '"args": {"message": "Check deployment", "fire_at": "2026-09-10T20:30:00"}, '
            '"reasoning": "User requested reminder."}'
        )
        res = call_agent("Remind me in 30 minutes to check deployment.")
        self.assertEqual(res.tool, "set_alarm")
        mock_save_alarm.assert_not_called()

    @patch("tools.calendar.create_event")
    @patch("agent.core._ollama_chat")
    def test_16_google_calendar_not_called_during_phase44(self, mock_ollama, mock_create_event):
        """16. Verify Google Calendar API is NOT called during LLM routing."""
        mock_ollama.return_value = (
            '{"tool": "set_alarm", '
            '"args": {"message": "Meeting reminder", "fire_at": "2026-09-11T09:00:00"}, '
            '"reasoning": "User requested reminder."}'
        )
        res = call_agent("Remind me tomorrow at 9 AM to prepare for meeting.")
        self.assertEqual(res.tool, "set_alarm")
        mock_create_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
