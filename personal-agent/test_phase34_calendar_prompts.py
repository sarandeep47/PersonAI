# personal-agent/test_phase34_calendar_prompts.py
import unittest
from unittest.mock import patch, MagicMock

from agent.prompts import AGENT_SYSTEM_PROMPT
from agent.schemas import ToolCall, TaskPlan
from agent.core import call_agent


class TestPhase34CalendarPrompts(unittest.TestCase):
    """Focused unit tests for Phase 3.4 LLM prompt integration for Google Calendar."""

    def test_prompt_contains_calendar_tool_descriptions(self):
        """1. Verify AGENT_SYSTEM_PROMPT contains schedule_calendar and list_calendar tool descriptions."""
        self.assertIn("schedule_calendar", AGENT_SYSTEM_PROMPT)
        self.assertIn("list_calendar", AGENT_SYSTEM_PROMPT)
        self.assertIn("Create a Google Calendar event", AGENT_SYSTEM_PROMPT)
        self.assertIn("Retrieve calendar events within a requested time window", AGENT_SYSTEM_PROMPT)

    def test_prompt_contains_decision_rules(self):
        """2. Verify AGENT_SYSTEM_PROMPT contains rules distinguishing scheduling vs listing."""
        self.assertIn("For Calendar requests", AGENT_SYSTEM_PROMPT)
        self.assertIn("schedule_calendar", AGENT_SYSTEM_PROMPT)
        self.assertIn("list_calendar", AGENT_SYSTEM_PROMPT)
        self.assertIn("Do NOT confuse listing vs scheduling", AGENT_SYSTEM_PROMPT)

    def test_prompt_contains_calendar_examples(self):
        """3. Verify AGENT_SYSTEM_PROMPT contains high-quality calendar examples."""
        self.assertIn("Example E — Schedule Calendar event", AGENT_SYSTEM_PROMPT)
        self.assertIn("Example F — List Calendar events", AGENT_SYSTEM_PROMPT)
        self.assertIn("Example G — Schedule Calendar event with attendee", AGENT_SYSTEM_PROMPT)
        self.assertIn("Example H — Calendar Query vs Schedule distinction", AGENT_SYSTEM_PROMPT)
        self.assertIn("Example I — TaskPlan with Calendar and Email", AGENT_SYSTEM_PROMPT)

    @patch("agent.core._ollama_chat")
    def test_llm_tool_selection_schedule_event(self, mock_ollama):
        """4. Verify schedule event input parses to schedule_calendar ToolCall."""
        mock_ollama.return_value = (
            '{"tool": "schedule_calendar", '
            '"args": {"title": "RAG project meeting", "date": "2026-10-16", "start_time": "15:00", "duration_minutes": 60}, '
            '"reasoning": "User requested scheduling a meeting."}'
        )

        res = call_agent("Schedule my RAG project meeting Friday at 3 PM for 1 hour.")

        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "schedule_calendar")
        self.assertEqual(res.args["title"], "RAG project meeting")
        self.assertEqual(res.args["date"], "2026-10-16")
        self.assertEqual(res.args["start_time"], "15:00")
        self.assertEqual(res.args["duration_minutes"], 60)

    @patch("agent.core._ollama_chat")
    def test_llm_tool_selection_list_events(self, mock_ollama):
        """5. Verify list events input parses to list_calendar ToolCall."""
        mock_ollama.return_value = (
            '{"tool": "list_calendar", '
            '"args": {"start_datetime": "2026-10-16T00:00:00", "end_datetime": "2026-10-16T23:59:59"}, '
            '"reasoning": "User asked to view tomorrow\'s schedule."}'
        )

        res = call_agent("What's on my calendar tomorrow?")

        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "list_calendar")

    @patch("agent.core._ollama_chat")
    def test_llm_tool_selection_query_event_distinction(self, mock_ollama):
        """6. Verify 'Do I have a meeting tomorrow?' query maps to list_calendar, not schedule_calendar."""
        mock_ollama.return_value = (
            '{"tool": "list_calendar", '
            '"args": {"start_datetime": "2026-10-16T00:00:00", "end_datetime": "2026-10-16T23:59:59"}, '
            '"reasoning": "User is querying existing calendar events."}'
        )

        res = call_agent("Do I have a project meeting tomorrow?")

        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "list_calendar")

    @patch("agent.core._ollama_chat")
    def test_llm_tool_selection_schedule_with_attendee(self, mock_ollama):
        """7. Verify scheduling with attendee maps to schedule_calendar with attendees argument."""
        mock_ollama.return_value = (
            '{"tool": "schedule_calendar", '
            '"args": {"title": "Project Meeting", "date": "2026-10-16", "start_time": "15:00", "duration_minutes": 30, "attendees": ["john@example.com"]}, '
            '"reasoning": "User requested scheduling a meeting with an attendee."}'
        )

        res = call_agent("Schedule a project meeting Friday at 3 PM with john@example.com.")

        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "schedule_calendar")
        self.assertEqual(res.args["attendees"], ["john@example.com"])

    @patch("agent.core._ollama_chat")
    def test_llm_tool_selection_multi_task_plan(self, mock_ollama):
        """8. Verify multi-task request (schedule + email) parses to TaskPlan."""
        mock_ollama.return_value = (
            '{"tasks": ['
            '{"tool": "schedule_calendar", "args": {"title": "RAG Meeting", "date": "2026-10-16", "start_time": "15:00", "duration_minutes": 30}, "reasoning": "Schedule meeting."}, '
            '{"tool": "send_email", "args": {"to": "john@example.com", "subject": "Meeting", "body": "Hi John"}, "reasoning": "Notify John."}'
            '], "reasoning": "Two tasks requested."}'
        )

        res = call_agent("Schedule a RAG meeting Friday at 3 PM and email john@example.com about it.")

        self.assertIsInstance(res, TaskPlan)
        self.assertEqual(len(res.tasks), 2)
        self.assertEqual(res.tasks[0].tool, "schedule_calendar")
        self.assertEqual(res.tasks[1].tool, "send_email")

    @patch("agent.core._ollama_chat")
    def test_existing_gmail_contact_tools_unaffected(self, mock_ollama):
        """9. Verify existing Gmail and contact tool selection remains unchanged."""
        mock_ollama.return_value = (
            '{"tool": "send_email", '
            '"args": {"to": "alice@example.com", "subject": "Hello", "body": "Hi Alice"}, '
            '"reasoning": "Send email requested."}'
        )

        res = call_agent("Send an email to alice@example.com saying Hello.")

        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "send_email")


if __name__ == "__main__":
    unittest.main()
