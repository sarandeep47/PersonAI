import unittest
from unittest.mock import patch
from datetime import datetime

from agent.schemas import ToolCall, SetAlarmArgs, ToolName
from agent.core import call_agent, validate_tool_call, parse_natural_datetime

REF_DT = datetime(2026, 9, 11, 19, 10, 0)


class TestRemainderTypoFix(unittest.TestCase):
    """Test suite for reminder/remainder typo handling and set_reminder normalization."""

    def test_01_tool_call_normalizes_set_reminder_to_set_alarm(self):
        """1. set_reminder tool name in JSON output is automatically normalized to set_alarm."""
        tc = ToolCall.model_validate_json(
            '{"tool": "set_reminder", "args": {"message": "Test reminder", "fire_at": "2026-09-11T19:11:00"}, "reasoning": "User set reminder"}'
        )
        self.assertEqual(tc.tool, "set_alarm")
        self.assertEqual(tc.args["message"], "Test reminder")

    def test_02_remainder_typo_duration_parsing(self):
        """2. 'can u set a remainder in 1 min' parses datetime 1 minute ahead."""
        parsed = parse_natural_datetime("can u set a remainder in 1 min", reference_datetime=REF_DT)
        self.assertEqual(parsed, "2026-09-11T19:11:00")

    def test_03_default_message_for_unspecified_reminder_text(self):
        """3. When message is empty or unspecified, validate_tool_call defaults to 'Reminder'."""
        tc = ToolCall(
            tool="set_alarm",
            args={"fire_at": "in 1 min"},
            reasoning="Testing empty message."
        )
        res = validate_tool_call(tc, user_message="can u set a remainder in 1 min", reference_datetime=REF_DT)
        self.assertEqual(res.tool, "set_alarm")
        self.assertEqual(res.args["message"], "Reminder")
        self.assertEqual(res.args["fire_at"], "2026-09-11T19:11:00")

    @patch("agent.core._ollama_chat")
    def test_04_call_agent_with_set_reminder_from_model(self, mock_ollama):
        """4. Model returning tool='set_reminder' produces set_alarm ToolCall without validation error."""
        mock_ollama.return_value = (
            '{"tool": "set_reminder", '
            '"args": {"message": "Check oven", "fire_at": "in 1 min"}, '
            '"reasoning": "User requested reminder in 1 min."}'
        )
        res = call_agent("can u set a remainder in 1 min", reference_datetime=REF_DT)
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "set_alarm")
        self.assertEqual(res.args["fire_at"], "2026-09-11T19:11:00")

    @patch("agent.core._ollama_chat")
    def test_05_call_agent_with_model_misclassification_fallback(self, mock_ollama):
        """5. If model misclassifies remainder prompt as tool='none', post-processor safeguard routes to set_alarm."""
        mock_ollama.return_value = (
            '{"tool": "none", '
            '"args": {"message": "What is the recipient email address?"}, '
            '"reasoning": "Missing recipient email address."}'
        )
        res = call_agent("can u set a remainder in 1 min", reference_datetime=REF_DT)
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "set_alarm")
        self.assertEqual(res.args["message"], "Reminder")
        self.assertEqual(res.args["fire_at"], "2026-09-11T19:11:00")

    def test_06_set_remainder_for_1940_pm(self):
        """6. 'set remainder for 19:40 pm' parses to concrete ISO datetime today at 19:40."""
        parsed = parse_natural_datetime("set remainder for 19:40 pm", reference_datetime=REF_DT)
        self.assertEqual(parsed, "2026-09-11T19:40:00")

    @patch("agent.core._ollama_chat")
    def test_07_call_agent_set_remainder_for_1940_pm(self, mock_ollama):
        """7. 'set remainder for 19:40 pm' produces set_alarm ToolCall at 19:40 even if model misclassifies."""
        mock_ollama.return_value = (
            '{"tool": "none", '
            '"args": {"message": "General conversational request without a specific search query or action."}, '
            '"reasoning": "General conversational request."}'
        )
        res = call_agent("set remainder for 19:40 pm", reference_datetime=REF_DT)
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "set_alarm")
        self.assertEqual(res.args["message"], "Reminder")
        self.assertEqual(res.args["fire_at"], "2026-09-11T19:40:00")

    @patch("agent.core._ollama_chat")
    def test_08_call_agent_overrides_list_calendar_misclassification(self, mock_ollama):
        """8. 'set remainder for 19:56 pm' overrides model list_calendar misclassification to set_alarm."""
        mock_ollama.return_value = (
            '{"tool": "list_calendar", '
            '"args": {"start_datetime": "2026-09-11T00:00:00", "end_datetime": "2026-09-11T23:59:59"}, '
            '"reasoning": "Checking calendar."}'
        )
        res = call_agent("set remainder for 19:56 pm", reference_datetime=REF_DT)
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "set_alarm")
        self.assertEqual(res.args["message"], "Reminder")
        self.assertEqual(res.args["fire_at"], "2026-09-11T19:56:00")

    @patch("agent.core._ollama_chat")
    def test_09_call_agent_extracts_message_with_to_clause(self, mock_ollama):
        """9. 'set remainder for 19:56 pm to check deployment' extracts message 'Check deployment'."""
        mock_ollama.return_value = (
            '{"tool": "list_calendar", '
            '"args": {"start_datetime": "2026-09-11T00:00:00", "end_datetime": "2026-09-11T23:59:59"}, '
            '"reasoning": "Checking calendar."}'
        )
        res = call_agent("set remainder for 19:56 pm to check deployment", reference_datetime=REF_DT)
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "set_alarm")
        self.assertEqual(res.args["message"], "Check deployment")
        self.assertEqual(res.args["fire_at"], "2026-09-11T19:56:00")

    @patch("agent.core._ollama_chat")
    def test_10_call_bob_at_2010_remainder_message(self, mock_ollama):
        """10. 'call bob at 20:10 remainder message' extracts task message 'Call bob' and time 20:10."""
        mock_ollama.return_value = (
            '{"tool": "set_alarm", '
            '"args": {"message": "reminder", "fire_at": "20:10"}, '
            '"reasoning": "Setting alarm."}'
        )
        res = call_agent("call bob at 20:10 remainder message", reference_datetime=REF_DT)
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "set_alarm")
        self.assertEqual(res.args["message"], "Call bob")
        self.assertEqual(res.args["fire_at"], "2026-09-11T20:10:00")

    def test_11_revise_alarm_task_change(self):
        """11. revise_alarm updates task message on feedback like 'Call Bob and Alice'."""
        from agent.core import revise_alarm
        orig = {"message": "Call bob", "fire_at": "2026-09-11T20:10:00"}
        revised = revise_alarm(orig, "Call Bob and Alice", reference_datetime=REF_DT)
        self.assertEqual(revised["message"], "Call bob and alice")
        self.assertEqual(revised["fire_at"], "2026-09-11T20:10:00")

    def test_12_revise_alarm_time_change(self):
        """12. revise_alarm updates time on feedback like 'at 8:30 PM'."""
        from agent.core import revise_alarm
        orig = {"message": "Call Bob", "fire_at": "2026-09-11T20:10:00"}
        revised = revise_alarm(orig, "at 8:30 PM", reference_datetime=REF_DT)
        self.assertEqual(revised["message"], "Call Bob")
        self.assertEqual(revised["fire_at"], "2026-09-11T20:30:00")


if __name__ == "__main__":
    unittest.main()
