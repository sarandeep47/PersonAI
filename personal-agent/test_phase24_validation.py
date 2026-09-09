"""
Phase 2.4 — Per-Task Validation tests.

Verifies that every ToolCall inside a TaskPlan goes through the existing
single-ToolCall validation pipeline, and that the whole plan is rejected
when any individual task fails validation.

All tests use unittest.mock to avoid a live Ollama server.
"""

import json
import unittest
from unittest.mock import patch, call

from agent.schemas import ToolCall, TaskPlan
from agent.core import (
    call_agent,
    validate_tool_call,
    _resolve_tool_call_contact,
    _validate_task_plan,
)

# ---------------------------------------------------------------------------
# JSON fixtures
# ---------------------------------------------------------------------------

# Two valid tasks — email address present in user message, export needs no args
VALID_TWO_TASK_JSON = json.dumps({
    "tasks": [
        {
            "tool": "search_inbox",
            "args": {"query": "invoice", "max_results": 5},
            "reasoning": "Search for invoice emails."
        },
        {
            "tool": "send_email",
            "args": {
                "to": "boss@example.com",
                "subject": "Invoice found",
                "body": "Hi Boss,\n\nHere is the invoice.\n\nBest regards,"
            },
            "reasoning": "Send the invoice email."
        }
    ],
    "reasoning": "Find and forward the invoice."
})

# Three tasks where task 2 has a hallucinated email (not in user message)
THREE_TASK_INVALID_MIDDLE_JSON = json.dumps({
    "tasks": [
        {
            "tool": "search_inbox",
            "args": {"query": "invoice", "max_results": 5},
            "reasoning": "Search step."
        },
        {
            "tool": "send_email",
            "args": {
                "to": "hallucinated@nowhere.com",   # NOT in user message
                "subject": "Bad email",
                "body": "Hi,\n\nThis should be blocked.\n\nBest regards,"
            },
            "reasoning": "This task has a hallucinated recipient."
        },
        {
            "tool": "export_contacts",
            "args": {},
            "reasoning": "Export contacts."
        }
    ],
    "reasoning": "Multi-step plan with a bad task in the middle."
})

# Task with bracketed placeholder in email body
PLACEHOLDER_TASK_JSON = json.dumps({
    "tasks": [
        {
            "tool": "send_email",
            "args": {
                "to": "boss@example.com",
                "subject": "Update",
                "body": "Hi Boss,\n\n[insert details here]\n\nBest regards,"
            },
            "reasoning": "Email with placeholder."
        }
    ],
    "reasoning": "Plan with a placeholder email."
})

# Single ToolCall (regression — must still work exactly as before)
SINGLE_TOOLCALL_JSON = json.dumps({
    "tool": "search_inbox",
    "args": {"query": "invoice", "max_results": 5},
    "reasoning": "Search for invoices."
})

# Three valid tasks — used to verify call counts
THREE_VALID_TASKS_JSON = json.dumps({
    "tasks": [
        {
            "tool": "search_inbox",
            "args": {"query": "invoice"},
            "reasoning": "Task 1."
        },
        {
            "tool": "export_contacts",
            "args": {},
            "reasoning": "Task 2."
        },
        {
            "tool": "delete_contact",
            "args": {"query": "old_contact"},
            "reasoning": "Task 3."
        }
    ],
    "reasoning": "Three independent tasks."
})


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class TestPhase24PerTaskValidation(unittest.TestCase):
    """Phase 2.4 — Per-task validation tests."""

    # ------------------------------------------------------------------ #
    # Test 1 — Valid TaskPlan passes validation intact
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=VALID_TWO_TASK_JSON)
    def test_valid_taskplan_passes_validation(self, _mock):
        """A TaskPlan with all valid tasks must survive and remain a TaskPlan."""
        # User message contains boss@example.com so send_email passes security
        result = call_agent(
            "Search for invoices and send one to boss@example.com"
        )
        self.assertIsInstance(result, TaskPlan,
            "A fully valid TaskPlan must still be returned as a TaskPlan after validation")
        self.assertEqual(len(result.tasks), 2,
            "Task count must be unchanged after validation")
        self.assertEqual(result.tasks[0].tool, "search_inbox",
            "Task order must be preserved")
        self.assertEqual(result.tasks[1].tool, "send_email",
            "Task order must be preserved")
        print("  [PASS] Test 1 — valid TaskPlan survives validation intact")

    # ------------------------------------------------------------------ #
    # Test 2 — Invalid task (hallucinated email) rejects whole plan
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=THREE_TASK_INVALID_MIDDLE_JSON)
    def test_invalid_task_rejects_whole_plan(self, _mock):
        """If any task fails validation, the entire plan must be rejected.
        The result must be a none-ToolCall, NOT a partial TaskPlan."""
        result = call_agent(
            # The hallucinated address is NOT in this user message
            "Search for invoices and export contacts"
        )
        self.assertIsInstance(result, ToolCall,
            "A plan with an invalid task must return a ToolCall, not a TaskPlan")
        self.assertEqual(result.tool, "none",
            "Whole-plan rejection must return tool='none'")
        self.assertIn("message", result.args,
            "Rejection ToolCall must include a message for the user")
        print("  [PASS] Test 2 — invalid middle task rejects entire plan")

    # ------------------------------------------------------------------ #
    # Test 3 — validate_tool_call called for every task
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=THREE_VALID_TASKS_JSON)
    def test_validation_called_for_every_task(self, _mock):
        """validate_tool_call must be called once for each task in the plan."""
        with patch(
            "agent.core.validate_tool_call",
            wraps=validate_tool_call
        ) as mock_validate:
            call_agent("Search invoices, export contacts, and delete old_contact")
            self.assertEqual(
                mock_validate.call_count, 3,
                "validate_tool_call must be called exactly once per task (3 tasks → 3 calls)"
            )
        print("  [PASS] Test 3 — validate_tool_call called for every task")

    # ------------------------------------------------------------------ #
    # Test 4 — Single ToolCall regression
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=SINGLE_TOOLCALL_JSON)
    def test_single_toolcall_regression(self, _mock):
        """A normal single ToolCall must still follow the existing pipeline unchanged."""
        with patch(
            "agent.core.validate_tool_call",
            wraps=validate_tool_call
        ) as mock_validate, patch(
            "agent.core._resolve_tool_call_contact",
            wraps=_resolve_tool_call_contact
        ) as mock_resolve:
            result = call_agent("Search my inbox for invoices")
            # Both pipeline functions must be called exactly once
            self.assertEqual(mock_validate.call_count, 1,
                "validate_tool_call must be called once for a single ToolCall")
            self.assertEqual(mock_resolve.call_count, 1,
                "_resolve_tool_call_contact must be called once for a single ToolCall")
        self.assertIsInstance(result, ToolCall)
        self.assertEqual(result.tool, "search_inbox")
        print("  [PASS] Test 4 — single ToolCall regression: pipeline unchanged")

    # ------------------------------------------------------------------ #
    # Test 5 — Contact resolution applied to TaskPlan tasks
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=VALID_TWO_TASK_JSON)
    def test_contact_resolution_applied_to_tasks(self, _mock):
        """_resolve_tool_call_contact must be called once per task."""
        with patch(
            "agent.core._resolve_tool_call_contact",
            wraps=_resolve_tool_call_contact
        ) as mock_resolve:
            call_agent(
                "Search for invoices and send one to boss@example.com"
            )
            self.assertEqual(
                mock_resolve.call_count, 2,
                "_resolve_tool_call_contact must be called once per task (2 tasks)"
            )
        print("  [PASS] Test 5 — contact resolution called for each task")

    # ------------------------------------------------------------------ #
    # Test 6 — Security: hallucinated email in TaskPlan is blocked
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=THREE_TASK_INVALID_MIDDLE_JSON)
    def test_hallucinated_email_in_task_blocked(self, _mock):
        """A hallucinated/untrusted email address inside a TaskPlan task must
        be blocked by the existing validate_tool_call security rule."""
        result = call_agent(
            # hallucinated@nowhere.com does NOT appear in this message
            "Search inbox and export my contacts"
        )
        # Security must reject the plan
        self.assertIsInstance(result, ToolCall)
        self.assertEqual(result.tool, "none",
            "Hallucinated email address in a task must reject the whole plan")
        print("  [PASS] Test 6 — hallucinated email in task blocks entire plan")

    # ------------------------------------------------------------------ #
    # Test 7 — Placeholder in email task rejects whole plan
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=PLACEHOLDER_TASK_JSON)
    def test_placeholder_in_task_rejects_plan(self, _mock):
        """An email task containing a bracketed placeholder must cause
        whole-plan rejection (no Ollama retry is attempted for sub-tasks)."""
        result = call_agent(
            "Send boss@example.com an email"
        )
        self.assertIsInstance(result, ToolCall)
        self.assertEqual(result.tool, "none",
            "A placeholder in any task must reject the whole plan")
        print("  [PASS] Test 7 — bracketed placeholder in task rejects entire plan")

    # ------------------------------------------------------------------ #
    # Test 8 — Task order preserved after validation
    # ------------------------------------------------------------------ #
    @patch("agent.core._ollama_chat", return_value=THREE_VALID_TASKS_JSON)
    def test_task_order_preserved(self, _mock):
        """Validated tasks must appear in the same order as the original plan."""
        result = call_agent(
            "Search invoices, export contacts, and delete old_contact"
        )
        self.assertIsInstance(result, TaskPlan)
        self.assertEqual(result.tasks[0].tool, "search_inbox")
        self.assertEqual(result.tasks[1].tool, "export_contacts")
        self.assertEqual(result.tasks[2].tool, "delete_contact")
        print("  [PASS] Test 8 — task order preserved after validation")

    # ------------------------------------------------------------------ #
    # Test 9 — _validate_task_plan unit test (direct call)
    # ------------------------------------------------------------------ #
    def test_validate_task_plan_direct_valid(self):
        """Direct unit test of _validate_task_plan with a valid plan."""
        plan = TaskPlan(
            tasks=[
                ToolCall(
                    tool="search_inbox",
                    args={"query": "test"},
                    reasoning="Search."
                ),
                ToolCall(
                    tool="export_contacts",
                    args={},
                    reasoning="Export."
                ),
            ],
            reasoning="Two tasks."
        )
        result = _validate_task_plan(
            plan,
            user_message="search test and export",
            history=[],
            chat_id=None,
            sender_name=None,
        )
        self.assertIsInstance(result, TaskPlan)
        self.assertEqual(len(result.tasks), 2)
        self.assertEqual(result.tasks[0].tool, "search_inbox")
        self.assertEqual(result.tasks[1].tool, "export_contacts")
        print("  [PASS] Test 9 — _validate_task_plan direct call with valid plan")

    # ------------------------------------------------------------------ #
    # Test 10 — _validate_task_plan rejects on bad task (direct call)
    # ------------------------------------------------------------------ #
    def test_validate_task_plan_direct_invalid(self):
        """Direct unit test of _validate_task_plan: a hallucinated email rejects the plan."""
        plan = TaskPlan(
            tasks=[
                ToolCall(
                    tool="send_email",
                    args={
                        "to": "ghost@nowhere.com",  # NOT in user_message
                        "subject": "Test",
                        "body": "Hi,\n\nTest.\n\nBest regards,"
                    },
                    reasoning="Hallucinated email."
                )
            ],
            reasoning="Bad plan."
        )
        result = _validate_task_plan(
            plan,
            user_message="send a test email",  # address not present
            history=[],
            chat_id=None,
            sender_name=None,
        )
        self.assertIsInstance(result, ToolCall)
        self.assertEqual(result.tool, "none")
        print("  [PASS] Test 10 — _validate_task_plan direct call rejects bad task")


if __name__ == "__main__":
    unittest.main(verbosity=2)
