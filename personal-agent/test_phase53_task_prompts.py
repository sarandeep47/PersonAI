import unittest
from unittest.mock import patch

from agent.prompts import AGENT_SYSTEM_PROMPT
from agent.schemas import ToolCall
from agent.core import call_agent


class TestPhase53TaskPrompts(unittest.TestCase):
    """Focused unit tests for Phase 5 Step 3 Task Tool Prompts."""

    def test_prompt_contains_task_tool_descriptions(self):
        """1. Verify AGENT_SYSTEM_PROMPT contains descriptions for all 4 task tools."""
        self.assertIn("11. add_task(title)", AGENT_SYSTEM_PROMPT)
        self.assertIn("12. list_tasks()", AGENT_SYSTEM_PROMPT)
        self.assertIn("13. complete_task(task_id)", AGENT_SYSTEM_PROMPT)
        self.assertIn("14. delete_task(task_id)", AGENT_SYSTEM_PROMPT)
        self.assertIn("add_task", AGENT_SYSTEM_PROMPT)
        self.assertIn("list_tasks", AGENT_SYSTEM_PROMPT)
        self.assertIn("complete_task", AGENT_SYSTEM_PROMPT)
        self.assertIn("delete_task", AGENT_SYSTEM_PROMPT)

    def test_prompt_contains_task_decision_rules(self):
        """2. Verify AGENT_SYSTEM_PROMPT contains rules for task tool selection."""
        self.assertIn("For Task requests:", AGENT_SYSTEM_PROMPT)
        self.assertIn("Use tool \"add_task\"", AGENT_SYSTEM_PROMPT)
        self.assertIn("Use tool \"list_tasks\"", AGENT_SYSTEM_PROMPT)
        self.assertIn("Use tool \"complete_task\"", AGENT_SYSTEM_PROMPT)
        self.assertIn("Use tool \"delete_task\"", AGENT_SYSTEM_PROMPT)
        self.assertIn("pass the title or referenced description as the task_id", AGENT_SYSTEM_PROMPT)
        self.assertIn("Do NOT invent fake random task IDs", AGENT_SYSTEM_PROMPT)

    def test_prompt_contains_required_intent_examples(self):
        """3. Verify AGENT_SYSTEM_PROMPT contains all required intent examples."""
        # ADD
        self.assertIn("Add finish my RAG docs to my tasks.", AGENT_SYSTEM_PROMPT)
        self.assertIn("Add buy groceries to my tasks.", AGENT_SYSTEM_PROMPT)
        self.assertIn("Remind me to finish the project.", AGENT_SYSTEM_PROMPT)

        # LIST
        self.assertIn("What tasks do I have?", AGENT_SYSTEM_PROMPT)
        self.assertIn("Show me my tasks.", AGENT_SYSTEM_PROMPT)
        self.assertIn("List my tasks.", AGENT_SYSTEM_PROMPT)

        # COMPLETE
        self.assertIn("Mark finish my RAG docs as done.", AGENT_SYSTEM_PROMPT)
        self.assertIn("I finished the RAG documentation.", AGENT_SYSTEM_PROMPT)
        self.assertIn("Mark that task complete.", AGENT_SYSTEM_PROMPT)

        # DELETE
        self.assertIn("Delete finish my RAG docs.", AGENT_SYSTEM_PROMPT)
        self.assertIn("Remove that task.", AGENT_SYSTEM_PROMPT)
        self.assertIn("Delete that task.", AGENT_SYSTEM_PROMPT)

    def test_existing_tools_retained_in_prompt(self):
        """4. Verify existing tool descriptions remain intact."""
        self.assertIn("send_email", AGENT_SYSTEM_PROMPT)
        self.assertIn("search_inbox", AGENT_SYSTEM_PROMPT)
        self.assertIn("read_email", AGENT_SYSTEM_PROMPT)
        self.assertIn("draft_reply", AGENT_SYSTEM_PROMPT)
        self.assertIn("export_contacts", AGENT_SYSTEM_PROMPT)
        self.assertIn("delete_contact", AGENT_SYSTEM_PROMPT)
        self.assertIn("rename_contact", AGENT_SYSTEM_PROMPT)
        self.assertIn("schedule_calendar", AGENT_SYSTEM_PROMPT)
        self.assertIn("list_calendar", AGENT_SYSTEM_PROMPT)
        self.assertIn("set_alarm", AGENT_SYSTEM_PROMPT)

    @patch("agent.core._ollama_chat")
    def test_llm_tool_selection_add_task(self, mock_ollama):
        """5. Verify 'Add finish my RAG docs to my tasks' parses to add_task ToolCall."""
        mock_ollama.return_value = (
            '{"tool": "add_task", '
            '"args": {"title": "finish my RAG docs"}, '
            '"reasoning": "User asked to add a task."}'
        )
        res = call_agent("Add finish my RAG docs to my tasks.")
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "add_task")
        self.assertEqual(res.args["title"], "finish my RAG docs")

    @patch("agent.core._ollama_chat")
    def test_llm_tool_selection_list_tasks(self, mock_ollama):
        """6. Verify 'What tasks do I have?' parses to list_tasks ToolCall."""
        mock_ollama.return_value = (
            '{"tool": "list_tasks", '
            '"args": {}, '
            '"reasoning": "User asked to view their tasks."}'
        )
        res = call_agent("What tasks do I have?")
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "list_tasks")

    @patch("agent.core._ollama_chat")
    def test_llm_tool_selection_complete_task(self, mock_ollama):
        """7. Verify 'Mark finish my RAG docs as done' parses to complete_task ToolCall."""
        mock_ollama.return_value = (
            '{"tool": "complete_task", '
            '"args": {"task_id": "finish my RAG docs"}, '
            '"reasoning": "User asked to complete task."}'
        )
        res = call_agent("Mark finish my RAG docs as done.")
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "complete_task")
        self.assertEqual(res.args["task_id"], "finish my RAG docs")

    @patch("agent.core._ollama_chat")
    def test_llm_tool_selection_delete_task(self, mock_ollama):
        """8. Verify 'Delete finish my RAG docs' parses to delete_task ToolCall."""
        mock_ollama.return_value = (
            '{"tool": "delete_task", '
            '"args": {"task_id": "finish my RAG docs"}, '
            '"reasoning": "User asked to delete task."}'
        )
        res = call_agent("Delete finish my RAG docs.")
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "delete_task")
        self.assertEqual(res.args["task_id"], "finish my RAG docs")
