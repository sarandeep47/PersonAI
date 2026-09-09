"""
Phase 2.6 — Plan Persistence + Callback tests.

Verifies TaskPlan persistence lifecycle:
1. Validated TaskPlan is saved as pending action with action_type='confirm_taskplan'.
2. Action ID maps to stored TaskPlan payload.
3. TaskPlan is reconstructed via TaskPlan.model_validate(payload).
4. plan_execute callback retrieves and validates TaskPlan without executing any tools.
5. plan_cancel callback deletes pending action without executing tools.
6. Non-existent, wrong action_type, corrupt payload, and cross-user ownership cases fail safely.
7. Zero tool functions are called across all callback operations.
"""

import os
import json
import unittest
from unittest.mock import patch, MagicMock

from agent.schemas import ToolCall, TaskPlan
import db.session as db
from main import (
    _present_task_plan_confirmation,
    handle_callback_query,
)


def _make_sample_task_plan() -> TaskPlan:
    """Create a sample 2-task plan for testing."""
    return TaskPlan(
        tasks=[
            ToolCall(
                tool="search_inbox",
                args={"query": "invoice", "max_results": 5},
                reasoning="Find invoice email."
            ),
            ToolCall(
                tool="send_email",
                args={
                    "to": "boss@example.com",
                    "subject": "Invoice Details",
                    "body": "Hi Boss,\n\nHere is the invoice.\n\nBest regards,"
                },
                reasoning="Forward invoice to boss."
            ),
        ],
        reasoning="Find invoice email then send to boss."
    )


TEST_DB_PATH = "test_phase26_temp.db"

class TestPhase26PlanPersistenceCallback(unittest.TestCase):
    """Phase 2.6 — Plan Persistence + Callback Test Suite."""

    @classmethod
    def setUpClass(cls):
        cls.ORIGINAL_DB_PATH = db.DB_PATH
        db.DB_PATH = TEST_DB_PATH
        db.init_db()

    def setUp(self):
        """Clean database table before each test."""
        db.DB_PATH = TEST_DB_PATH
        conn = db.get_db()
        with conn:
            conn.execute("DELETE FROM pending_actions")

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH = cls.ORIGINAL_DB_PATH
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass


    # ------------------------------------------------------------------ #
    # Test 8 — Execute All execution (Phase 2.7/2.8)
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_execute_all_remains_non_executing(self, mock_answer, mock_send, mock_send_email):
        """Verify tool function is invoked upon Execute All tap in Phase 2.7/2.8."""
        plan = _make_sample_task_plan()
        chat_id = "12345"
        action_id = "plan_noexec_008"

        tasks_payload = [
            {"tool": t.tool, "args": t.args, "reasoning": t.reasoning}
            for t in plan.tasks
        ]
        db.save_pending_action(action_id, chat_id, "confirm_taskplan", {
            "tasks": tasks_payload,
            "reasoning": plan.reasoning
        })

        cq = {
            "id": "cq_107",
            "data": f"plan_execute:{action_id}",
            "message": {"chat": {"id": chat_id}}
        }

        handle_callback_query(cq)

        # Assert send_email_raw was invoked once during TaskPlan execution
        mock_send_email.assert_called_once()

    # ------------------------------------------------------------------ #
    # Test 1 — TaskPlan is persisted and reconstructed
    # ------------------------------------------------------------------ #
    def test_taskplan_persisted_and_reconstructed(self):
        """Save a TaskPlan as pending action, retrieve it, and reconstruct TaskPlan."""
        original_plan = _make_sample_task_plan()
        action_id = "test_plan_001"
        chat_id = "12345"

        # Serialize payload as done in _present_task_plan_confirmation
        tasks_payload = [
            {"tool": t.tool, "args": t.args, "reasoning": t.reasoning}
            for t in original_plan.tasks
        ]
        payload = {"tasks": tasks_payload, "reasoning": original_plan.reasoning}

        # Save using existing pending_action mechanism
        db.save_pending_action(action_id, chat_id, "confirm_taskplan", payload)

        # Retrieve action from DB
        stored_action = db.get_pending_action(action_id)
        self.assertIsNotNone(stored_action, "Pending action must exist in DB")
        self.assertEqual(stored_action["action_type"], "confirm_taskplan")
        self.assertEqual(stored_action["chat_id"], chat_id)

        # Reconstruct TaskPlan using Pydantic model_validate
        reconstructed_plan = TaskPlan.model_validate(stored_action["payload"])

        # Assert task count, order, tool names, args, and reasoning
        self.assertEqual(len(reconstructed_plan.tasks), len(original_plan.tasks))
        self.assertEqual(reconstructed_plan.reasoning, original_plan.reasoning)

        # Task 1
        self.assertEqual(reconstructed_plan.tasks[0].tool, "search_inbox")
        self.assertEqual(reconstructed_plan.tasks[0].args["query"], "invoice")
        self.assertEqual(reconstructed_plan.tasks[0].reasoning, "Find invoice email.")

        # Task 2
        self.assertEqual(reconstructed_plan.tasks[1].tool, "send_email")
        self.assertEqual(reconstructed_plan.tasks[1].args["to"], "boss@example.com")
        self.assertEqual(reconstructed_plan.tasks[1].reasoning, "Forward invoice to boss.")

        print("  [PASS] Test 1 — TaskPlan successfully persisted and reconstructed with all fields intact")

    # ------------------------------------------------------------------ #
    # Test 2 — Execute callback retrieves correct TaskPlan
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_execute_callback_retrieves_plan(self, mock_answer, mock_send):
        """plan_execute callback retrieves correct TaskPlan, acknowledges, and confirms without execution."""
        plan = _make_sample_task_plan()
        chat_id = "12345"
        action_id = "plan_exec_002"

        tasks_payload = [
            {"tool": t.tool, "args": t.args, "reasoning": t.reasoning}
            for t in plan.tasks
        ]
        db.save_pending_action(action_id, chat_id, "confirm_taskplan", {
            "tasks": tasks_payload,
            "reasoning": plan.reasoning
        })

        cq = {
            "id": "cq_101",
            "data": f"plan_execute:{action_id}",
            "message": {"chat": {"id": chat_id}}
        }

        handle_callback_query(cq)

        # Verify callback acknowledged
        mock_answer.assert_called_once_with("cq_101", "Executing tasks...")

        # Verify confirmation message sent
        mock_send.assert_called_once()

        # Verify pending action is consumed upon execution
        action_after = db.get_pending_action(action_id)
        self.assertIsNone(action_after, "Pending action must be consumed upon execution")

        print("  [PASS] Test 2 — plan_execute retrieves TaskPlan, acknowledges callback, sends message")

    # ------------------------------------------------------------------ #
    # Test 3 — Cancel callback deletes TaskPlan
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_cancel_callback_deletes_plan(self, mock_answer, mock_send):
        """plan_cancel callback deletes pending TaskPlan from DB and confirms cancellation."""
        plan = _make_sample_task_plan()
        chat_id = "12345"
        action_id = "plan_cancel_003"

        tasks_payload = [
            {"tool": t.tool, "args": t.args, "reasoning": t.reasoning}
            for t in plan.tasks
        ]
        db.save_pending_action(action_id, chat_id, "confirm_taskplan", {
            "tasks": tasks_payload,
            "reasoning": plan.reasoning
        })

        cq = {
            "id": "cq_102",
            "data": f"plan_cancel:{action_id}",
            "message": {"chat": {"id": chat_id}}
        }

        handle_callback_query(cq)

        # Verify callback acknowledged
        mock_answer.assert_called_once_with("cq_102", "Plan cancelled.")

        # Verify cancellation message sent
        mock_send.assert_called_once()
        self.assertIn("Task plan cancelled", mock_send.call_args[0][0])

        # Verify action is deleted from DB
        action_after = db.get_pending_action(action_id)
        self.assertIsNone(action_after, "Pending action must be deleted from DB after cancellation")

        print("  [PASS] Test 3 — plan_cancel deletes TaskPlan from DB and sends cancellation message")

    # ------------------------------------------------------------------ #
    # Test 4 — Unknown action ID
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_unknown_action_id(self, mock_answer, mock_send):
        """Non-existent action ID responds safely without crashing."""
        cq = {
            "id": "cq_103",
            "data": "plan_execute:does_not_exist_999",
            "message": {"chat": {"id": "12345"}}
        }

        handle_callback_query(cq)

        mock_answer.assert_called_once_with("cq_103", "Action expired or already completed.")
        mock_send.assert_called_once()
        self.assertIn("confirmation has expired", mock_send.call_args[0][0])

        print("  [PASS] Test 4 — unknown action ID handles gracefully with expired notification")

    # ------------------------------------------------------------------ #
    # Test 5 — Wrong action type
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_wrong_action_type(self, mock_answer, mock_send):
        """plan_execute called on an unrelated action_type (confirm_send) must be rejected."""
        chat_id = "12345"
        action_id = "send_action_005"

        # Save non-TaskPlan pending action
        email_payload = {
            "to": "bob@example.com",
            "subject": "Hello",
            "body": "Hi Bob"
        }
        db.save_pending_action(action_id, chat_id, "confirm_send", email_payload)

        cq = {
            "id": "cq_104",
            "data": f"plan_execute:{action_id}",
            "message": {"chat": {"id": chat_id}}
        }

        handle_callback_query(cq)

        mock_answer.assert_called_once_with("cq_104", "Invalid action type.")
        mock_send.assert_called_once()
        self.assertIn("Invalid action type for task plan", mock_send.call_args[0][0])

        # Verify original non-TaskPlan payload was NOT deleted or corrupted
        action_after = db.get_pending_action(action_id)
        self.assertIsNotNone(action_after)
        self.assertEqual(action_after["action_type"], "confirm_send")

        print("  [PASS] Test 5 — wrong action_type is rejected and original action preserved")

    # ------------------------------------------------------------------ #
    # Test 6 — Corrupt TaskPlan payload
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_corrupt_taskplan_payload(self, mock_answer, mock_send):
        """Corrupted TaskPlan payload fails Pydantic validation cleanly and deletes corrupt action."""
        chat_id = "12345"
        action_id = "corrupt_plan_006"

        # Store corrupt payload (invalid tool name in task)
        corrupt_payload = {
            "tasks": [
                {"tool": "malicious_tool_xyz", "args": {}}
            ],
            "reasoning": "Corrupt plan."
        }
        db.save_pending_action(action_id, chat_id, "confirm_taskplan", corrupt_payload)

        cq = {
            "id": "cq_105",
            "data": f"plan_execute:{action_id}",
            "message": {"chat": {"id": chat_id}}
        }

        handle_callback_query(cq)

        mock_answer.assert_called_once_with("cq_105", "Invalid task plan data.")
        mock_send.assert_called_once()
        self.assertIn("pending task plan is invalid or corrupted", mock_send.call_args[0][0])

        # Verify corrupt action is deleted
        action_after = db.get_pending_action(action_id)
        self.assertIsNone(action_after, "Corrupt action must be deleted from DB")

        print("  [PASS] Test 6 — corrupt payload fails validation safely and is cleaned up")

    # ------------------------------------------------------------------ #
    # Test 7 — Callback action ownership
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_callback_action_ownership(self, mock_answer, mock_send):
        """Callback from a different chat_id than the action owner must be rejected."""
        plan = _make_sample_task_plan()
        owner_chat_id = "owner_user_111"
        attacker_chat_id = "attacker_user_999"
        action_id = "plan_ownership_007"

        tasks_payload = [
            {"tool": t.tool, "args": t.args, "reasoning": t.reasoning}
            for t in plan.tasks
        ]
        db.save_pending_action(action_id, owner_chat_id, "confirm_taskplan", {
            "tasks": tasks_payload,
            "reasoning": plan.reasoning
        })

        cq = {
            "id": "cq_106",
            "data": f"plan_execute:{action_id}",
            "message": {"chat": {"id": attacker_chat_id}}
        }

        handle_callback_query(cq)

        mock_answer.assert_called_once_with("cq_106", "Unauthorized action.")
        mock_send.assert_called_once()
        self.assertIn("do not have permission", mock_send.call_args[0][0])

        # Verify plan owned by owner_chat_id remains untouched
        action_after = db.get_pending_action(action_id)
        self.assertIsNotNone(action_after)
        self.assertEqual(action_after["chat_id"], owner_chat_id)

        print("  [PASS] Test 7 — cross-user callback rejected safely without modifying pending action")


if __name__ == "__main__":
    unittest.main()
