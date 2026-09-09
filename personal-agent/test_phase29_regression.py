"""
Phase 2.9 — Testing & Regression test suite.

Verifies end-to-end behavior of Phase 2 multi-task flow and single-task regression:
1. Single-action request produces ToolCall.
2. Multi-action request produces TaskPlan.
3. TaskPlan preserves task order.
4. Every task is validated before execution.
5. Invalid task prevents unsafe partial execution.
6. TaskPlan confirmation generated correctly.
7. Execute callback retrieves correct pending plan.
8. Pending plan belongs only to originating chat.
9. Execute All runs tasks sequentially.
10. Successful tasks all execute.
11. First failure stops subsequent tasks.
12. Remaining tasks are marked skipped.
13. Duplicate Execute All cannot execute tasks twice.
14. Cancel removes pending action.
15. Expired/missing action is handled safely.
16. Wrong action type is rejected safely.
17. Corrupt pending payload is handled safely.
18. Unexpected tool exceptions become failures.
19. Malformed tool results do not crash execution.
20. Telegram summary failure does not re-execute tasks.
21. Existing single-tool execution still works.
22. Side-effecting tools are never automatically retried.
"""

import os
import json
import unittest
from unittest.mock import patch, MagicMock, call

from agent.schemas import ToolCall, TaskPlan
import db.session as db
from agent.core import _validate_task_plan
from main import (
    _format_task_plan_confirmation,
    _present_task_plan_confirmation,
    execute_single_tool_call,
    execute_task_plan,
    handle_callback_query,
)

TEST_DB_PATH = "test_phase29_temp.db"


def _make_sample_task_plan() -> TaskPlan:
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


def _make_three_task_plan() -> TaskPlan:
    return TaskPlan(
        tasks=[
            ToolCall(tool="search_inbox", args={"query": "invoices"}, reasoning="Step 1"),
            ToolCall(tool="send_email", args={"to": "a@b.com", "subject": "S", "body": "B"}, reasoning="Step 2"),
            ToolCall(tool="export_contacts", args={}, reasoning="Step 3"),
        ],
        reasoning="Three step plan"
    )


class TestPhase29Regression(unittest.TestCase):
    """Phase 2.9 — End-to-End Testing & Regression Test Suite."""

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

    # 1. Single-action request produces ToolCall
    def test_01_single_action_produces_toolcall(self):
        json_str = '{"tool": "search_inbox", "args": {"query": "invoice"}, "reasoning": "Find invoice"}'
        res = ToolCall.model_validate_json(json_str)
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "search_inbox")

    # 2. Multi-action request produces TaskPlan
    def test_02_multi_action_produces_taskplan(self):
        json_str = json.dumps({
            "tasks": [
                {"tool": "search_inbox", "args": {"query": "invoice"}, "reasoning": "r1"},
                {"tool": "send_email", "args": {"to": "a@b.com", "subject": "s", "body": "b"}, "reasoning": "r2"}
            ],
            "reasoning": "Multi plan"
        })
        res = TaskPlan.model_validate_json(json_str)
        self.assertIsInstance(res, TaskPlan)
        self.assertEqual(len(res.tasks), 2)

    # 3. TaskPlan preserves task order
    def test_03_taskplan_preserves_task_order(self):
        plan = _make_three_task_plan()
        self.assertEqual(plan.tasks[0].tool, "search_inbox")
        self.assertEqual(plan.tasks[1].tool, "send_email")
        self.assertEqual(plan.tasks[2].tool, "export_contacts")

    # 4. Every task is validated before execution
    @patch("agent.core.validate_tool_call")
    def test_04_every_task_validated_before_execution(self, mock_val):
        mock_val.side_effect = lambda tc, text="", history=None, chat_id=None: tc
        plan = _make_three_task_plan()
        res = _validate_task_plan(plan, "user message", [], "123", None)
        self.assertEqual(mock_val.call_count, 3)
        self.assertIsInstance(res, TaskPlan)

    # 5. Invalid task prevents unsafe partial execution
    @patch("agent.core.validate_tool_call")
    def test_05_invalid_task_prevents_unsafe_execution(self, mock_val):
        t1 = ToolCall(tool="search_inbox", args={"query": "test"})
        t2_invalid = ToolCall(tool="none", args={"message": "Blocked"})
        mock_val.side_effect = [t1, t2_invalid]

        plan = _make_sample_task_plan()
        res = _validate_task_plan(plan, "user message", [], "123", None)
        # Whole plan is rejected and returns single 'none' ToolCall
        self.assertIsInstance(res, ToolCall)
        self.assertEqual(res.tool, "none")

    # 6. TaskPlan confirmation generated correctly
    def test_06_taskplan_confirmation_generated_correctly(self):
        plan = _make_sample_task_plan()
        msg = _format_task_plan_confirmation(plan)
        self.assertIn("Planned Actions", msg)
        self.assertIn("Search emails", msg)
        self.assertIn("Send email", msg)
        self.assertIn("Proceed?", msg)

    # 7. Execute callback retrieves the correct pending plan
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.execute_task_plan")
    def test_07_execute_callback_retrieves_correct_plan(self, mock_exec_plan, mock_answer, mock_send):
        mock_exec_plan.return_value = "Execution summary"
        plan = _make_sample_task_plan()
        chat_id = "12345"
        action_id = "plan_test_007"

        tasks_payload = [{"tool": t.tool, "args": t.args, "reasoning": t.reasoning} for t in plan.tasks]
        db.save_pending_action(action_id, chat_id, "confirm_taskplan", {"tasks": tasks_payload, "reasoning": plan.reasoning})

        cq = {"id": "cq_007", "data": f"plan_execute:{action_id}", "message": {"chat": {"id": chat_id}}}
        handle_callback_query(cq)

        mock_exec_plan.assert_called_once()
        retrieved_plan = mock_exec_plan.call_args[0][0]
        self.assertEqual(len(retrieved_plan.tasks), 2)
        self.assertEqual(retrieved_plan.tasks[0].tool, "search_inbox")

    # 8. Pending plan belongs only to the originating chat
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.execute_task_plan")
    def test_08_pending_plan_ownership_enforced(self, mock_exec_plan, mock_answer, mock_send):
        plan = _make_sample_task_plan()
        owner_id = "owner_123"
        attacker_id = "attacker_999"
        action_id = "plan_test_008"

        tasks_payload = [{"tool": t.tool, "args": t.args, "reasoning": t.reasoning} for t in plan.tasks]
        db.save_pending_action(action_id, owner_id, "confirm_taskplan", {"tasks": tasks_payload, "reasoning": plan.reasoning})

        cq = {"id": "cq_008", "data": f"plan_execute:{action_id}", "message": {"chat": {"id": attacker_id}}}
        handle_callback_query(cq)

        mock_answer.assert_called_once_with("cq_008", "Unauthorized action.")
        mock_exec_plan.assert_not_called()
        self.assertIsNotNone(db.get_pending_action(action_id))

    # 9. Execute All runs tasks sequentially
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    def test_09_execute_all_runs_tasks_sequentially(self, mock_fetch, mock_send):
        order = []
        mock_fetch.side_effect = lambda: order.append("task_1") or []
        mock_send.side_effect = lambda to, s, b, attachment_path=None: order.append("task_2") or True

        plan = _make_sample_task_plan()
        execute_task_plan(plan, "12345")
        self.assertEqual(order, ["task_1", "task_2"])

    # 10. Successful tasks all execute
    @patch("main.send_telegram_document")
    @patch("main.db.export_contacts_csv")
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    def test_10_successful_tasks_all_execute(self, mock_fetch, mock_send, mock_export, mock_doc):
        mock_fetch.return_value = []
        mock_send.return_value = True
        mock_export.return_value = "contacts.csv"

        plan = _make_three_task_plan()
        summary = execute_task_plan(plan, "12345")
        self.assertIn("3/3 actions completed", summary)

    # 11. First failure stops subsequent tasks
    @patch("main.db.export_contacts_csv")
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    def test_11_first_failure_stops_subsequent_tasks(self, mock_fetch, mock_send, mock_export):
        mock_fetch.return_value = []
        mock_send.return_value = False  # Task 2 fails!

        plan = _make_three_task_plan()
        summary = execute_task_plan(plan, "12345")

        mock_fetch.assert_called_once()
        mock_send.assert_called_once()
        mock_export.assert_not_called()
        self.assertIn("1/3 actions completed", summary)

    # 12. Remaining tasks are marked skipped
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    def test_12_remaining_tasks_marked_skipped(self, mock_fetch, mock_send):
        mock_fetch.side_effect = RuntimeError("Failed Task 1")

        plan = _make_sample_task_plan()
        summary = execute_task_plan(plan, "12345")
        self.assertIn("❌ *1. Search emails*", summary)
        self.assertIn("⏭️ *2. Send email*", summary)
        self.assertIn("0/2 actions completed", summary)

    # 13. Duplicate Execute All cannot execute tasks twice
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_13_duplicate_execute_all_cannot_execute_twice(self, mock_answer, mock_send, mock_fetch, mock_send_email):
        mock_fetch.return_value = []
        mock_send_email.return_value = True

        plan = _make_sample_task_plan()
        chat_id = "12345"
        action_id = "plan_dup_013"

        tasks_payload = [{"tool": t.tool, "args": t.args, "reasoning": t.reasoning} for t in plan.tasks]
        db.save_pending_action(action_id, chat_id, "confirm_taskplan", {"tasks": tasks_payload, "reasoning": plan.reasoning})

        cq1 = {"id": "c1", "data": f"plan_execute:{action_id}", "message": {"chat": {"id": chat_id}}}
        cq2 = {"id": "c2", "data": f"plan_execute:{action_id}", "message": {"chat": {"id": chat_id}}}

        handle_callback_query(cq1)
        handle_callback_query(cq2)

        self.assertEqual(mock_send_email.call_count, 1)

    # 14. Cancel removes pending action
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_14_cancel_removes_pending_action(self, mock_answer, mock_send):
        plan = _make_sample_task_plan()
        chat_id = "12345"
        action_id = "plan_cancel_014"

        tasks_payload = [{"tool": t.tool, "args": t.args, "reasoning": t.reasoning} for t in plan.tasks]
        db.save_pending_action(action_id, chat_id, "confirm_taskplan", {"tasks": tasks_payload, "reasoning": plan.reasoning})

        cq = {"id": "cq_cancel", "data": f"plan_cancel:{action_id}", "message": {"chat": {"id": chat_id}}}
        handle_callback_query(cq)

        self.assertIsNone(db.get_pending_action(action_id))

    # 15. Expired/missing action is handled safely
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_15_expired_missing_action_handled_safely(self, mock_answer, mock_send):
        cq = {"id": "cq_missing", "data": "plan_execute:non_existent_id", "message": {"chat": {"id": "12345"}}}
        handle_callback_query(cq)
        mock_answer.assert_called_once_with("cq_missing", "Action expired or already completed.")

    # 16. Wrong action type is rejected safely
    @patch("main.send_email_raw")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_16_wrong_action_type_rejected_safely(self, mock_answer, mock_send, mock_send_email):
        db.save_pending_action("action_016", "12345", "confirm_send", {"to": "a@b.com"})
        cq = {"id": "cq_016", "data": "plan_execute:action_016", "message": {"chat": {"id": "12345"}}}
        handle_callback_query(cq)
        mock_answer.assert_called_once_with("cq_016", "Invalid action type.")
        mock_send_email.assert_not_called()

    # 17. Corrupt pending payload is handled safely
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_17_corrupt_pending_payload_handled_safely(self, mock_answer, mock_send):
        db.save_pending_action("corrupt_017", "12345", "confirm_taskplan", {"tasks": "invalid_not_list"})
        cq = {"id": "cq_017", "data": "plan_execute:corrupt_017", "message": {"chat": {"id": "12345"}}}
        handle_callback_query(cq)
        mock_answer.assert_called_once_with("cq_017", "Invalid task plan data.")
        self.assertIsNone(db.get_pending_action("corrupt_017"))

    # 18. Unexpected tool exceptions become failures
    @patch("main.fetch_unread_emails")
    def test_18_unexpected_tool_exception_becomes_failure(self, mock_fetch):
        mock_fetch.side_effect = KeyError("Unexpected dict key missing")
        tc = ToolCall(tool="search_inbox", args={"query": "test"})
        success, msg = execute_single_tool_call(tc, "12345")
        self.assertFalse(success)
        self.assertIn("Execution error", msg)

    # 19. Malformed tool results do not crash execution
    @patch("main.fetch_unread_emails")
    def test_19_malformed_tool_results_do_not_crash(self, mock_fetch):
        mock_fetch.return_value = None  # None instead of list!
        tc = ToolCall(tool="search_inbox", args={"query": "test"})
        success, msg = execute_single_tool_call(tc, "12345")
        self.assertFalse(success)
        self.assertIn("Unexpected result format", msg)

    # 20. Telegram summary failure does not re-execute tasks
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_20_telegram_summary_failure_does_not_reexecute(self, mock_answer, mock_send, mock_fetch, mock_send_email):
        mock_fetch.return_value = []
        mock_send_email.return_value = True
        mock_send.side_effect = RuntimeError("Telegram connection reset")

        plan = _make_sample_task_plan()
        action_id = "plan_020"
        tasks_payload = [{"tool": t.tool, "args": t.args, "reasoning": t.reasoning} for t in plan.tasks]
        db.save_pending_action(action_id, "12345", "confirm_taskplan", {"tasks": tasks_payload, "reasoning": plan.reasoning})

        cq = {"id": "cq_020", "data": f"plan_execute:{action_id}", "message": {"chat": {"id": "12345"}}}
        handle_callback_query(cq)

        self.assertEqual(mock_send_email.call_count, 1)
        self.assertIsNone(db.get_pending_action(action_id))

    # 21. Existing single-tool execution still works
    @patch("main.fetch_unread_emails")
    def test_21_existing_single_tool_execution_works(self, mock_fetch):
        mock_fetch.return_value = [{"subject": "hello", "body": "world"}]
        tc = ToolCall(tool="search_inbox", args={"query": "hello"})
        success, msg = execute_single_tool_call(tc, "12345")
        self.assertTrue(success)
        self.assertIn("Found 1 matching email(s)", msg)

    # 22. Side-effecting tools are never automatically retried
    @patch("main.send_email_raw")
    def test_22_side_effecting_tools_never_automatically_retried(self, mock_send_email):
        mock_send_email.return_value = False
        tc = ToolCall(tool="send_email", args={"to": "a@b.com", "subject": "S", "body": "B"})
        plan = TaskPlan(tasks=[tc], reasoning="test")

        summary = execute_task_plan(plan, "12345")
        self.assertEqual(mock_send_email.call_count, 1)
        self.assertIn("0/1 actions completed", summary)


if __name__ == "__main__":
    unittest.main()
