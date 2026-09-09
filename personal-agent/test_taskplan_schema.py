"""
Phase 2.1 — TaskPlan schema tests.
Validates the new TaskPlan model and confirms ToolCall behaviour is unchanged.
"""

import unittest
from pydantic import ValidationError
from agent.schemas import ToolCall, TaskPlan


class TestTaskPlanSchema(unittest.TestCase):

    # ------------------------------------------------------------------ #
    # Test 1 — Single-task plan
    # ------------------------------------------------------------------ #
    def test_single_task_plan(self):
        """TaskPlan wrapping one ToolCall should validate without errors."""
        plan = TaskPlan(
            tasks=[
                ToolCall(
                    tool="send_email",
                    args={"to": "boss@example.com", "subject": "Update", "body": "Here is the update."},
                    reasoning="Send the requested email"
                )
            ],
            reasoning="One planned action"
        )
        self.assertEqual(len(plan.tasks), 1)
        self.assertEqual(plan.tasks[0].tool, "send_email")
        self.assertEqual(plan.reasoning, "One planned action")
        print("✅ Test 1 passed — single-task TaskPlan")

    # ------------------------------------------------------------------ #
    # Test 2 — Multi-task plan
    # ------------------------------------------------------------------ #
    def test_multi_task_plan(self):
        """TaskPlan wrapping two ToolCall objects should validate correctly."""
        plan = TaskPlan(
            tasks=[
                ToolCall(
                    tool="search_inbox",
                    args={"query": "from:boss invoice", "max_results": 5},
                    reasoning="Find relevant emails first"
                ),
                ToolCall(
                    tool="send_email",
                    args={"to": "colleague@example.com", "subject": "FYI", "body": "See attached."},
                    reasoning="Forward the information"
                )
            ],
            reasoning="Two planned actions"
        )
        self.assertEqual(len(plan.tasks), 2)
        self.assertEqual(plan.tasks[0].tool, "search_inbox")
        self.assertEqual(plan.tasks[1].tool, "send_email")
        self.assertEqual(plan.reasoning, "Two planned actions")
        print("✅ Test 2 passed — multi-task TaskPlan")

    # ------------------------------------------------------------------ #
    # Test 3 — ToolCall still works independently
    # ------------------------------------------------------------------ #
    def test_toolcall_independent(self):
        """ToolCall must continue to function exactly as before."""
        tc = ToolCall(
            tool="none",
            args={"message": "How can I help?"},
            reasoning="No action required"
        )
        self.assertEqual(tc.tool, "none")
        self.assertEqual(tc.args["message"], "How can I help?")
        self.assertEqual(tc.reasoning, "No action required")

        # Default reasoning still works
        tc_default = ToolCall(tool="export_contacts")
        self.assertEqual(tc_default.reasoning, "No reasoning provided.")
        print("✅ Test 3 passed — ToolCall works independently, defaults intact")

    # ------------------------------------------------------------------ #
    # Test 4 — Invalid task data is rejected
    # ------------------------------------------------------------------ #
    def test_invalid_task_rejected(self):
        """Pydantic must reject a TaskPlan whose tasks list contains an invalid tool name."""
        with self.assertRaises(ValidationError):
            TaskPlan(
                tasks=[
                    {"tool": "nonexistent_tool", "args": {}, "reasoning": "bad tool"}
                ],
                reasoning="Should be rejected"
            )
        print("✅ Test 4 passed — invalid tool name correctly rejected by Pydantic")

    def test_missing_reasoning_rejected(self):
        """TaskPlan.reasoning is required; omitting it must raise ValidationError."""
        with self.assertRaises(ValidationError):
            TaskPlan(
                tasks=[
                    ToolCall(tool="read_email", args={"email_id": "abc123"})
                ]
                # reasoning intentionally omitted
            )
        print("✅ Test 4b passed — missing TaskPlan.reasoning correctly rejected")


if __name__ == "__main__":
    unittest.main(verbosity=2)
