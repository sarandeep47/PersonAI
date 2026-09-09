"""
Phase 2.7 — Multi-Task Execution tests.

Verifies TaskPlan execution behavior:
1. Sequential execution in exact task order.
2. All tasks execute when all succeed.
3. Immediate stop on first task failure (remaining tasks marked as skipped).
4. Formatted Telegram summary output.
5. One-shot execution / duplicate callback protection.
6. Ownership enforcement (wrong chat_id cannot execute).
7. Schema/corrupt plan protection (0 tools executed).
8. Wrong action_type protection (0 tools executed).
9. Single tool call regression preservation.
10. Zero automatic retries of failing side-effecting tools.
"""

import os
import json
import unittest
from unittest.mock import patch, MagicMock, call

from agent.schemas import ToolCall, TaskPlan
import db.session as db
from main import (
    execute_single_tool_call,
    execute_task_plan,
    handle_callback_query,
)

TEST_DB_PATH = "test_phase27_temp.db"


def _make_sample_two_task_plan() -> TaskPlan:
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
                    "body": "Hi Boss,\n\nHere is the invoice."
                },
                reasoning="Forward invoice to boss."
            ),
        ],
        reasoning="Find invoice email then send to boss."
    )


def _make_sample_three_task_plan() -> TaskPlan:
    return TaskPlan(
        tasks=[
            ToolCall(tool="search_inbox", args={"query": "invoices"}, reasoning="Step 1."),
            ToolCall(
                tool="send_email",
                args={"to": "a@b.com", "subject": "S1", "body": "B1"},
                reasoning="Step 2."
            ),
            ToolCall(tool="export_contacts", args={}, reasoning="Step 3."),
        ],
        reasoning="Three step plan."
    )


class TestPhase27Execution(unittest.TestCase):
    """Phase 2.7 — Multi-Task Execution Test Suite."""

    @classmethod
    def setUpClass(cls):
        cls.ORIGINAL_DB_PATH = db.DB_PATH
        db.DB_PATH = TEST_DB_PATH
        db.init_db()

    def setUp(self):
        db.DB_PATH = TEST_DB_PATH
        conn = db.get_db()
        with conn:
            conn.execute("DELETE FROM pending_actions")
            conn.execute("DELETE FROM conversation_history")

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH = cls.ORIGINAL_DB_PATH
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # Test 1 — Two-task plan executes sequentially
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    def test_two_task_plan_executes_sequentially(self, mock_fetch, mock_send_email):
        """Tasks execute strictly in their listed order (Task 1 before Task 2)."""
        execution_order = []

        def fake_fetch():
            execution_order.append("search_inbox")
            return [{"subject": "invoice", "body": "here", "sender": "x@y.com"}]

        def fake_send(to, subject, body, attachment_path=None):
            execution_order.append("send_email")
            return True

        mock_fetch.side_effect = fake_fetch
        mock_send_email.side_effect = fake_send

        plan = _make_sample_two_task_plan()
        summary = execute_task_plan(plan, "12345")

        self.assertEqual(execution_order, ["search_inbox", "send_email"],
            "Task 1 (search_inbox) must execute before Task 2 (send_email)")
        self.assertIn("2/2 actions completed", summary)

        print("  [PASS] Test 1 — two-task plan executed sequentially in exact order")

    # ------------------------------------------------------------------ #
    # Test 2 — All tasks execute when successful
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_document")
    @patch("main.db.export_contacts_csv")
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    def test_all_tasks_execute_when_successful(self, mock_fetch, mock_send_email, mock_export, mock_send_doc):
        """When all tasks succeed, all tasks are executed and reported as successes."""
        mock_fetch.return_value = [{"subject": "invoices", "body": "test"}]
        mock_send_email.return_value = True
        mock_export.return_value = "contacts_export.csv"

        plan = _make_sample_three_task_plan()
        summary = execute_task_plan(plan, "12345")

        self.assertIn("✅ *1. Search emails*", summary)
        self.assertIn("✅ *2. Send email*", summary)
        self.assertIn("✅ *3. Export contacts*", summary)
        self.assertIn("3/3 actions completed", summary)

        print("  [PASS] Test 2 — all 3 tasks executed successfully and reported")

    # ------------------------------------------------------------------ #
    # Test 3 — Stop on failure
    # ------------------------------------------------------------------ #
    @patch("main.send_telegram_document")
    @patch("main.db.export_contacts_csv")
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    def test_stop_on_failure(self, mock_fetch, mock_send_email, mock_export, mock_send_doc):
        """If Task 2 fails, execution halts immediately and Task 3 is marked skipped."""
        mock_fetch.return_value = [{"subject": "invoices", "body": "test"}]
        mock_send_email.return_value = False  # Task 2 fails!
        mock_export.return_value = "contacts_export.csv"

        plan = _make_sample_three_task_plan()
        summary = execute_task_plan(plan, "12345")

        # Task 1 called
        mock_fetch.assert_called_once()
        # Task 2 called
        mock_send_email.assert_called_once()
        # Task 3 NOT called
        mock_export.assert_not_called()

        self.assertIn("✅ *1. Search emails*", summary)
        self.assertIn("❌ *2. Send email*", summary)
        self.assertIn("⏭️ *3. Export contacts*", summary)
        self.assertIn("1/3 actions completed", summary)

        print("  [PASS] Test 3 — execution stopped immediately on Task 2 failure; Task 3 skipped")

    # ------------------------------------------------------------------ #
    # Test 4 — Final summary format
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    def test_final_summary_format(self, mock_fetch, mock_send_email):
        """Final summary contains header, icons, display names, output messages, and completion count."""
        mock_fetch.return_value = []
        mock_send_email.return_value = True

        plan = _make_sample_two_task_plan()
        summary = execute_task_plan(plan, "12345")

        self.assertTrue(summary.startswith("📋 *Task Plan Execution Result*"))
        self.assertIn("✅ *1. Search emails*", summary)
        self.assertIn("No emails found matching `invoice`", summary)
        self.assertIn("✅ *2. Send email*", summary)
        self.assertIn("Email successfully sent to `boss@example.com`", summary)
        self.assertIn("2/2 actions completed.", summary)

        print("  [PASS] Test 4 — final summary formatted cleanly with icons and details")

    # ------------------------------------------------------------------ #
    # Test 5 — Duplicate Execute All protection (One-shot execution)
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_duplicate_execute_all_protection(self, mock_answer, mock_send, mock_fetch, mock_send_email):
        """Two Execute All callbacks for the same action_id execute the plan AT MOST ONCE."""
        mock_fetch.return_value = [{"subject": "invoice", "body": "details"}]
        mock_send_email.return_value = True

        plan = _make_sample_two_task_plan()
        chat_id = "12345"
        action_id = "plan_dup_005"

        tasks_payload = [
            {"tool": t.tool, "args": t.args, "reasoning": t.reasoning}
            for t in plan.tasks
        ]
        db.save_pending_action(action_id, chat_id, "confirm_taskplan", {
            "tasks": tasks_payload,
            "reasoning": plan.reasoning
        })

        cq1 = {
            "id": "cq_first",
            "data": f"plan_execute:{action_id}",
            "message": {"chat": {"id": chat_id}}
        }
        cq2 = {
            "id": "cq_second",
            "data": f"plan_execute:{action_id}",
            "message": {"chat": {"id": chat_id}}
        }

        # First tap -> executes plan
        handle_callback_query(cq1)

        # Second tap -> action_id is deleted from DB, rejected safely
        handle_callback_query(cq2)

        # Verify send_email_raw was called EXACTLY ONCE
        self.assertEqual(mock_send_email.call_count, 1, "send_email_raw must be called exactly once")
        self.assertEqual(mock_fetch.call_count, 1, "fetch_unread_emails must be called exactly once")

        # Second callback answered with expired/completed warning
        mock_answer.assert_has_calls([
            call("cq_first", "Executing tasks..."),
            call("cq_second", "Action expired or already completed.")
        ])

        print("  [PASS] Test 5 — duplicate Execute All tap safely rejected; plan executed exactly once")

    # ------------------------------------------------------------------ #
    # Test 6 — Wrong chat cannot execute
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_wrong_chat_cannot_execute(self, mock_answer, mock_send, mock_send_email):
        """A user from a different chat_id cannot execute another user's pending TaskPlan."""
        plan = _make_sample_two_task_plan()
        owner_chat_id = "owner_111"
        attacker_chat_id = "attacker_999"
        action_id = "plan_wrong_chat_006"

        tasks_payload = [
            {"tool": t.tool, "args": t.args, "reasoning": t.reasoning}
            for t in plan.tasks
        ]
        db.save_pending_action(action_id, owner_chat_id, "confirm_taskplan", {
            "tasks": tasks_payload,
            "reasoning": plan.reasoning
        })

        cq = {
            "id": "cq_attack",
            "data": f"plan_execute:{action_id}",
            "message": {"chat": {"id": attacker_chat_id}}
        }

        handle_callback_query(cq)

        mock_answer.assert_called_once_with("cq_attack", "Unauthorized action.")
        mock_send_email.assert_not_called()

        # Action preserved for owner
        self.assertIsNotNone(db.get_pending_action(action_id))

        print("  [PASS] Test 6 — wrong chat ID rejected safely with 0 tool executions")

    # ------------------------------------------------------------------ #
    # Test 7 — Invalid/corrupt plan cannot execute
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_corrupt_plan_cannot_execute(self, mock_answer, mock_send, mock_send_email):
        """Corrupted payload fails Pydantic validation cleanly and executes 0 tools."""
        chat_id = "12345"
        action_id = "plan_corrupt_007"

        corrupt_payload = {
            "tasks": [{"tool": "unknown_tool_xyz", "args": {}}],
            "reasoning": "Corrupt plan."
        }
        db.save_pending_action(action_id, chat_id, "confirm_taskplan", corrupt_payload)

        cq = {
            "id": "cq_corrupt",
            "data": f"plan_execute:{action_id}",
            "message": {"chat": {"id": chat_id}}
        }

        handle_callback_query(cq)

        mock_answer.assert_called_once_with("cq_corrupt", "Invalid task plan data.")
        mock_send_email.assert_not_called()
        self.assertIsNone(db.get_pending_action(action_id))

        print("  [PASS] Test 7 — corrupt plan rejected cleanly with 0 tool executions")

    # ------------------------------------------------------------------ #
    # Test 8 — Wrong action type cannot execute
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_wrong_action_type_cannot_execute(self, mock_answer, mock_send, mock_send_email):
        """plan_execute targeting action_type='confirm_send' executes 0 tools."""
        chat_id = "12345"
        action_id = "send_action_008"

        db.save_pending_action(action_id, chat_id, "confirm_send", {
            "to": "a@b.com", "subject": "s", "body": "b"
        })

        cq = {
            "id": "cq_wrong_type",
            "data": f"plan_execute:{action_id}",
            "message": {"chat": {"id": chat_id}}
        }

        handle_callback_query(cq)

        mock_answer.assert_called_once_with("cq_wrong_type", "Invalid action type.")
        mock_send_email.assert_not_called()

        print("  [PASS] Test 8 — wrong action_type rejected with 0 tool executions")

    # ------------------------------------------------------------------ #
    # Test 9 — Single ToolCall regression
    # ------------------------------------------------------------------ #
    @patch("main.fetch_unread_emails")
    def test_single_toolcall_regression(self, mock_fetch):
        """Normal single ToolCall execution via execute_single_tool_call functions properly."""
        mock_fetch.return_value = [{"subject": "meeting", "body": "notes"}]

        tc = ToolCall(tool="search_inbox", args={"query": "meeting"}, reasoning="Search meeting")
        success, msg = execute_single_tool_call(tc, "12345")

        self.assertTrue(success)
        self.assertIn("Found 1 matching email(s)", msg)

        print("  [PASS] Test 9 — single ToolCall execution works as expected")

    # ------------------------------------------------------------------ #
    # Test 10 — No automatic side-effect retry
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    def test_no_automatic_side_effect_retry(self, mock_send_email):
        """Failing side-effecting tool (send_email) is invoked exactly once and NOT retried."""
        mock_send_email.return_value = False

        tc = ToolCall(
            tool="send_email",
            args={"to": "x@y.com", "subject": "Hi", "body": "Hello"},
            reasoning="Send mail"
        )
        plan = TaskPlan(tasks=[tc], reasoning="One task plan")

        summary = execute_task_plan(plan, "12345")

        self.assertEqual(mock_send_email.call_count, 1, "send_email_raw must be invoked exactly once (no retry)")
        self.assertIn("0/1 actions completed", summary)
        self.assertIn("❌ *1. Send email*", summary)

        print("  [PASS] Test 10 — failing side-effecting tool is called once with zero automatic retries")


if __name__ == "__main__":
    unittest.main()
