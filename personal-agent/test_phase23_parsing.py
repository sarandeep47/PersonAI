"""
Phase 2.3 — Multi-Task Parsing tests.

Tests that call_agent() can parse both ToolCall and TaskPlan responses from
Ollama, without invoking any real tools or a live Ollama server.

Uses unittest.mock.patch to replace _ollama_chat so tests run offline.
"""

import json
import unittest
from unittest.mock import patch
from pydantic import ValidationError

from agent.schemas import ToolCall, TaskPlan
from agent.core import call_agent


# ---------------------------------------------------------------------------
# Helpers — canonical JSON strings the mock Ollama will return
# ---------------------------------------------------------------------------

SINGLE_TOOLCALL_JSON = json.dumps({
    "tool": "search_inbox",
    "args": {"query": "invoice", "max_results": 5},
    "reasoning": "User asked to search for invoice emails."
})

TWO_TASK_PLAN_JSON = json.dumps({
    "tasks": [
        {
            "tool": "search_inbox",
            "args": {"query": "invoice", "max_results": 5},
            "reasoning": "Search for invoice emails first."
        },
        {
            "tool": "send_email",
            "args": {
                "to": "boss@example.com",
                "subject": "Invoice",
                "body": "Hi,\n\nPlease find the invoice.\n\nBest regards,"
            },
            "reasoning": "Send the invoice email as requested."
        }
    ],
    "reasoning": "Search for the invoice and then send the requested email."
})

ONE_TASK_PLAN_JSON = json.dumps({
    "tasks": [
        {
            "tool": "export_contacts",
            "args": {},
            "reasoning": "User requested contacts export."
        }
    ],
    "reasoning": "Single task wrapped in a TaskPlan."
})

MALFORMED_JSON = "{ this is not valid json !! }"

UNKNOWN_TOOL_JSON = json.dumps({
    "tool": "nonexistent_tool",
    "args": {},
    "reasoning": "This tool does not exist."
})


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class TestPhase23Parsing(unittest.TestCase):
    """Phase 2.3 — Parsing layer tests (no live Ollama, no tool execution)."""

    # ------------------------------------------------------------------ #
    # Test 1 — Single ToolCall still parses correctly
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=SINGLE_TOOLCALL_JSON)
    def test_single_toolcall_parses(self, _mock):
        """A single-tool Ollama response must still return a ToolCall."""
        result = call_agent("Search my inbox for invoices")
        self.assertIsInstance(result, ToolCall,
            "call_agent() must return a ToolCall for a single-tool response")
        self.assertEqual(result.tool, "search_inbox")
        self.assertEqual(result.args["query"], "invoice")
        print("  [PASS] Test 1 — single ToolCall parses correctly")

    # ------------------------------------------------------------------ #
    # Test 2 — TaskPlan with two tasks parses correctly
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=TWO_TASK_PLAN_JSON)
    def test_two_task_plan_parses(self, _mock):
        """A two-task Ollama response must return a TaskPlan with correct structure."""
        result = call_agent("Search for invoices and send one to boss@example.com")
        self.assertIsInstance(result, TaskPlan,
            "call_agent() must return a TaskPlan for a multi-task response")
        self.assertEqual(len(result.tasks), 2,
            "TaskPlan must contain exactly 2 tasks")
        # Each task must be a ToolCall
        for task in result.tasks:
            self.assertIsInstance(task, ToolCall,
                "Every task inside a TaskPlan must be a ToolCall instance")
        self.assertEqual(result.tasks[0].tool, "search_inbox")
        self.assertEqual(result.tasks[1].tool, "send_email")
        self.assertEqual(result.tasks[1].args["to"], "boss@example.com")
        self.assertEqual(result.reasoning,
            "Search for the invoice and then send the requested email.")
        print("  [PASS] Test 2 — two-task TaskPlan parses correctly")

    # ------------------------------------------------------------------ #
    # Test 3 — TaskPlan with ONE task is structurally valid (no minimum)
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=ONE_TASK_PLAN_JSON)
    def test_one_task_plan_parses(self, _mock):
        """A TaskPlan with a single task must parse — no artificial minimum."""
        result = call_agent("Export my contacts please")
        self.assertIsInstance(result, TaskPlan,
            "A TaskPlan with one task must parse successfully")
        self.assertEqual(len(result.tasks), 1)
        self.assertEqual(result.tasks[0].tool, "export_contacts")
        print("  [PASS] Test 3 — single-task TaskPlan parses (no minimum enforced)")

    # ------------------------------------------------------------------ #
    # Test 4a — Malformed JSON follows existing error handling
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=MALFORMED_JSON)
    def test_malformed_json_falls_back_to_none(self, _mock):
        """Malformed JSON must not raise an unhandled exception; it must
        fall through to the none-ToolCall final fallback."""
        # The mock returns malformed JSON on every call (initial + retry).
        result = call_agent("Do something unparseable")
        # Must not raise; must return some ToolCall (the none fallback)
        self.assertIsInstance(result, (ToolCall, TaskPlan),
            "A result must always be returned even for malformed JSON")
        if isinstance(result, ToolCall):
            # The final fallback is a none ToolCall with an error message
            self.assertEqual(result.tool, "none",
                "Unrecoverable malformed JSON must produce tool='none'")
        print("  [PASS] Test 4a — malformed JSON falls back gracefully to none ToolCall")

    # ------------------------------------------------------------------ #
    # Test 4b — Invalid tool name in ToolCall JSON is rejected by Pydantic
    # ------------------------------------------------------------------ #
    def test_invalid_toolcall_json_rejected_by_pydantic(self):
        """Pydantic must reject a ToolCall JSON with an unknown tool name."""
        with self.assertRaises(ValidationError):
            ToolCall.model_validate_json(UNKNOWN_TOOL_JSON)
        print("  [PASS] Test 4b — unknown tool name correctly rejected by Pydantic")

    # ------------------------------------------------------------------ #
    # Test 5 — No tool execution occurs during parsing
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=TWO_TASK_PLAN_JSON)
    @patch("agent.core.db.get_contacts", return_value=[])
    @patch("agent.core.db.get_user_profile", return_value=None)
    @patch("agent.core.db.extract_user_name_from_text", return_value=None)
    def test_no_tool_execution_on_taskplan(self, _mock_name, _mock_profile, _mock_contacts, _mock_chat):
        """Parsing a TaskPlan must not execute any tools — only parse, validate, and return."""
        # Include boss@example.com so Phase 2.4 validation passes the send_email task
        result = call_agent(
            "Search for invoices and send one to boss@example.com",
            chat_id="test_chat"
        )
        # The only outcome must be a parsed+validated TaskPlan — no email sent
        self.assertIsInstance(result, TaskPlan,
            "A TaskPlan Ollama response must return a TaskPlan, never execute tools")
        self.assertEqual(len(result.tasks), 2)
        print("  [PASS] Test 5 — no tool execution during TaskPlan parsing")


    # ------------------------------------------------------------------ #
    # Test 6 — isinstance() correctly distinguishes the two return types
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat")
    def test_isinstance_dispatch(self, mock_chat):
        """isinstance() must correctly identify ToolCall vs TaskPlan results."""
        mock_chat.return_value = SINGLE_TOOLCALL_JSON
        result_tc = call_agent("Single task")
        self.assertTrue(isinstance(result_tc, ToolCall))
        self.assertFalse(isinstance(result_tc, TaskPlan))

        mock_chat.return_value = TWO_TASK_PLAN_JSON
        # Include boss@example.com so the send_email task passes Phase 2.4 validation
        result_tp = call_agent("Search for invoices and send one to boss@example.com")
        self.assertTrue(isinstance(result_tp, TaskPlan))
        self.assertFalse(isinstance(result_tp, ToolCall))
        print("  [PASS] Test 6 — isinstance() correctly identifies return types")

    # ------------------------------------------------------------------ #
    # Test 7 — Per-task validation IS called (Phase 2.4 behaviour)
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=TWO_TASK_PLAN_JSON)
    def test_per_task_validation_called_for_taskplan(self, _mock):
        """Phase 2.4: validate_tool_call and _resolve_tool_call_contact must be
        called for each task in a TaskPlan. _post_process_contact_intent must
        NOT be called at the plan level (it is message-level, not task-level)."""
        with patch("agent.core.validate_tool_call", wraps=__import__("agent.core", fromlist=["validate_tool_call"]).validate_tool_call) as mock_validate, \
             patch("agent.core._resolve_tool_call_contact", wraps=__import__("agent.core", fromlist=["_resolve_tool_call_contact"])._resolve_tool_call_contact) as mock_resolve, \
             patch("agent.core._post_process_contact_intent") as mock_post:
            # Use a user message containing the email so validation passes
            result = call_agent("Search for invoices and send one to boss@example.com")
            # validate_tool_call must be called once per task (2 tasks)
            self.assertEqual(mock_validate.call_count, 2,
                "validate_tool_call must be called once per task")
            # _resolve_tool_call_contact called once per task
            self.assertEqual(mock_resolve.call_count, 2,
                "_resolve_tool_call_contact must be called once per task")
            # _post_process_contact_intent must NOT run at the plan level
            mock_post.assert_not_called()
        print("  [PASS] Test 7 — per-task validation called; plan-level post-process skipped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
