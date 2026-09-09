"""
Phase 2.8 — Multi-Task Error Handling tests.

Verifies robust error handling during TaskPlan execution:
1. Unexpected tool exceptions become standard task failures.
2. Exception in task N causes subsequent tasks to be skipped.
3. Bot execution flow does not crash on unexpected tool exceptions.
4. Failure is formatted correctly in TaskPlan summary.
5. Malformed tool results/inputs are handled safely without crashing.
6. User-facing error messages sanitize OAuth tokens, API keys, and stack traces.
7. Telegram summary message delivery failure does not cause task re-execution or action restoration.
8. Duplicate Execute All taps remain strictly prevented (one-shot execution).
9. Existing single ToolCall execution path functions properly.
10. No automatic retries occur after side-effecting tool failures.
"""

import os
import json
import unittest
from unittest.mock import patch, MagicMock, call

from agent.schemas import ToolCall, TaskPlan
import db.session as db
from main import (
    _sanitize_error_message,
    execute_single_tool_call,
    execute_task_plan,
    handle_callback_query,
)

TEST_DB_PATH = "test_phase28_temp.db"


def _make_sample_three_task_plan() -> TaskPlan:
    return TaskPlan(
        tasks=[
            ToolCall(tool="search_inbox", args={"query": "invoice"}, reasoning="Task 1"),
            ToolCall(tool="send_email", args={"to": "x@y.com", "subject": "S", "body": "B"}, reasoning="Task 2"),
            ToolCall(tool="export_contacts", args={}, reasoning="Task 3"),
        ],
        reasoning="Three step plan"
    )


class TestPhase28ErrorHandling(unittest.TestCase):
    """Phase 2.8 — Multi-Task Error Handling Test Suite."""

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
    # Test 1 — Unexpected tool exception becomes task failure
    # ------------------------------------------------------------------ #
    @patch("main.fetch_unread_emails")
    def test_unexpected_tool_exception_becomes_task_failure(self, mock_fetch):
        """An unhandled exception inside a tool function returns (False, error_msg)."""
        mock_fetch.side_effect = RuntimeError("Network connection lost")

        tc = ToolCall(tool="search_inbox", args={"query": "test"}, reasoning="Search inbox")
        success, msg = execute_single_tool_call(tc, "12345")

        self.assertFalse(success, "Unexpected exception must result in task failure (success=False)")
        self.assertIn("Execution error", msg, "Failure message must describe the error")

        print("  [PASS] Test 1 — unexpected tool exception safely caught as task failure")

    # ------------------------------------------------------------------ #
    # Test 2 — Exception in task 2 skips task 3
    # ------------------------------------------------------------------ #
    @patch("main.db.export_contacts_csv")
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    def test_exception_in_task_2_skips_task_3(self, mock_fetch, mock_send_email, mock_export):
        """When Task 2 raises an unexpected exception, Task 3 is marked skipped and not executed."""
        mock_fetch.return_value = [{"subject": "invoice", "body": "test"}]
        mock_send_email.side_effect = Exception("SMTP server crashed")

        plan = _make_sample_three_task_plan()
        summary = execute_task_plan(plan, "12345")

        mock_fetch.assert_called_once()
        mock_send_email.assert_called_once()
        mock_export.assert_not_called()

        self.assertIn("✅ *1. Search emails*", summary)
        self.assertIn("❌ *2. Send email*", summary)
        self.assertIn("⏭️ *3. Export contacts*", summary)
        self.assertIn("1/3 actions completed", summary)

        print("  [PASS] Test 2 — unexpected exception in Task 2 skipped Task 3")

    # ------------------------------------------------------------------ #
    # Test 3 — Bot does not crash on tool exception
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    def test_bot_does_not_crash_on_tool_exception(self, mock_send_email):
        """Executing a TaskPlan when a tool throws unexpected errors returns safely without raising."""
        mock_send_email.side_effect = ZeroDivisionError("Division by zero in tool")

        tc = ToolCall(tool="send_email", args={"to": "a@b.com", "subject": "S", "body": "B"})
        plan = TaskPlan(tasks=[tc], reasoning="Test plan")

        try:
            summary = execute_task_plan(plan, "12345")
            self.assertIsNotNone(summary)
            self.assertIn("0/1 actions completed", summary)
        except Exception as e:
            self.fail(f"execute_task_plan raised unexpected exception: {e}")

        print("  [PASS] Test 3 — bot execution flow completed safely without crashing")

    # ------------------------------------------------------------------ #
    # Test 4 — Failure appears in summary
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    def test_failure_appears_in_summary(self, mock_send_email):
        """Task failure details appear under the ❌ icon in the formatted Telegram summary."""
        mock_send_email.side_effect = RuntimeError("Service unavailable")

        plan = TaskPlan(
            tasks=[ToolCall(tool="send_email", args={"to": "boss@domain.com", "subject": "X", "body": "Y"})],
            reasoning="Send email"
        )
        summary = execute_task_plan(plan, "12345")

        self.assertIn("❌ *1. Send email*", summary)
        self.assertIn("Execution error", summary)
        self.assertIn("0/1 actions completed", summary)

        print("  [PASS] Test 4 — task failure formatted correctly with ❌ in summary")

    # ------------------------------------------------------------------ #
    # Test 5 — Malformed tool result handled safely
    # ------------------------------------------------------------------ #
    @patch("main.fetch_unread_emails")
    def test_malformed_tool_result_handled_safely(self, mock_fetch):
        """Tool returning invalid payload format (e.g. string instead of list) returns failure cleanly."""
        mock_fetch.return_value = "invalid_string_not_a_list"  # Malformed result!

        tc = ToolCall(tool="search_inbox", args={"query": "invoice"})
        success, msg = execute_single_tool_call(tc, "12345")

        self.assertFalse(success)
        self.assertIn("Unexpected result format", msg)

        print("  [PASS] Test 5 — malformed tool return value handled safely without crashing")

    # ------------------------------------------------------------------ #
    # Test 6 — Sensitive information protection
    # ------------------------------------------------------------------ #
    def test_sensitive_information_protection(self):
        """_sanitize_error_message redacts OAuth tokens, credentials, and internal stack trace paths."""
        err1 = Exception("OAuth token bearer_secret_token_12345 invalid")
        sanitized1 = _sanitize_error_message(err1)

        self.assertNotIn("bearer_secret_token_12345", sanitized1, "OAuth token must be redacted from error message")
        self.assertIn("Unable to complete this action due to an unexpected error", sanitized1)

        err2 = Exception("sqlite3.OperationalError: no such table: secret_db_table at line 42 in file /app/db.py")
        sanitized2 = _sanitize_error_message(err2)

        self.assertNotIn("/app/db.py", sanitized2, "Internal file paths must be redacted")
        self.assertNotIn("secret_db_table", sanitized2, "Database internals must be redacted")

        print("  [PASS] Test 6 — sensitive tokens and stack traces sanitized from user-facing errors")

    # ------------------------------------------------------------------ #
    # Test 7 — Telegram summary failure does not re-execute tasks
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_telegram_summary_failure_does_not_reexecute(self, mock_answer, mock_send_msg, mock_fetch, mock_send_email):
        """If send_telegram_message throws when sending the summary, tasks are NOT re-executed."""
        mock_fetch.return_value = [{"subject": "invoice", "body": "b"}]
        mock_send_email.return_value = True

        # Telegram delivery fails during summary delivery!
        mock_send_msg.side_effect = RuntimeError("Telegram API gateway timeout")

        plan = _make_sample_three_task_plan()
        chat_id = "12345"
        action_id = "plan_tgerr_007"

        tasks_payload = [
            {"tool": t.tool, "args": t.args, "reasoning": t.reasoning}
            for t in plan.tasks
        ]
        db.save_pending_action(action_id, chat_id, "confirm_taskplan", {
            "tasks": tasks_payload,
            "reasoning": plan.reasoning
        })

        cq = {
            "id": "cq_tgerr",
            "data": f"plan_execute:{action_id}",
            "message": {"chat": {"id": chat_id}}
        }

        # handle_callback_query should catch Telegram send error, log it, and NOT crash or re-execute
        try:
            handle_callback_query(cq)
        except Exception as e:
            self.fail(f"handle_callback_query raised exception on Telegram send error: {e}")

        # Tasks executed exactly ONCE
        self.assertEqual(mock_send_email.call_count, 1)
        self.assertEqual(mock_fetch.call_count, 1)

        # Pending action remains deleted (NOT restored)
        self.assertIsNone(db.get_pending_action(action_id))

        print("  [PASS] Test 7 — Telegram delivery failure logged safely without re-executing tasks")

    # ------------------------------------------------------------------ #
    # Test 8 — Duplicate Execute All remains prevented
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    @patch("main.fetch_unread_emails")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    def test_duplicate_execute_all_remains_prevented(self, mock_answer, mock_send_msg, mock_fetch, mock_send_email):
        """Duplicate callbacks for the same action_id execute at most once."""
        mock_fetch.return_value = [{"subject": "invoice", "body": "b"}]
        mock_send_email.return_value = True

        plan = _make_sample_three_task_plan()
        chat_id = "12345"
        action_id = "plan_dup_008"

        tasks_payload = [
            {"tool": t.tool, "args": t.args, "reasoning": t.reasoning}
            for t in plan.tasks
        ]
        db.save_pending_action(action_id, chat_id, "confirm_taskplan", {
            "tasks": tasks_payload,
            "reasoning": plan.reasoning
        })

        cq1 = {"id": "cq_1", "data": f"plan_execute:{action_id}", "message": {"chat": {"id": chat_id}}}
        cq2 = {"id": "cq_2", "data": f"plan_execute:{action_id}", "message": {"chat": {"id": chat_id}}}

        handle_callback_query(cq1)
        handle_callback_query(cq2)

        self.assertEqual(mock_send_email.call_count, 1, "send_email_raw must be executed exactly once")

        print("  [PASS] Test 8 — duplicate Execute All taps strictly prevented")

    # ------------------------------------------------------------------ #
    # Test 9 — Existing single ToolCall still works
    # ------------------------------------------------------------------ #
    @patch("main.fetch_unread_emails")
    def test_existing_single_toolcall_still_works(self, mock_fetch):
        """Single ToolCall execution path works properly with valid input."""
        mock_fetch.return_value = [{"subject": "test", "body": "body"}]

        tc = ToolCall(tool="search_inbox", args={"query": "test"}, reasoning="Single test")
        success, msg = execute_single_tool_call(tc, "12345")

        self.assertTrue(success)
        self.assertIn("Found 1 matching email(s)", msg)

        print("  [PASS] Test 9 — single ToolCall execution path verified")

    # ------------------------------------------------------------------ #
    # Test 10 — No automatic retry after failure
    # ------------------------------------------------------------------ #
    @patch("main.send_email_raw")
    def test_no_automatic_retry_after_failure(self, mock_send_email):
        """Failing side-effecting tool call is called exactly once with zero automatic retries."""
        mock_send_email.side_effect = RuntimeError("SMTP connection timeout")

        tc = ToolCall(tool="send_email", args={"to": "a@b.com", "subject": "S", "body": "B"})
        plan = TaskPlan(tasks=[tc], reasoning="Single task plan")

        summary = execute_task_plan(plan, "12345")

        self.assertEqual(mock_send_email.call_count, 1, "send_email_raw must be invoked exactly once")
        self.assertIn("0/1 actions completed", summary)

        print("  [PASS] Test 10 — failing tool called exactly once with zero automatic retries")


if __name__ == "__main__":
    unittest.main()
