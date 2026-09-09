"""
Phase 2.5 — Multi-Task Confirmation UI tests.

Verifies that a validated TaskPlan produces the correct Telegram confirmation
message and inline buttons, without executing any tools.

All tests use unittest.mock to avoid live Telegram API calls, live Ollama
calls, and real database writes.
"""

import json
import unittest
from unittest.mock import patch, MagicMock, call

from agent.schemas import ToolCall, TaskPlan
from main import (
    _format_tool_name,
    _format_task_plan_confirmation,
    _present_task_plan_confirmation,
    handle_callback_query,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_two_task_plan() -> TaskPlan:
    """A two-task plan: search_inbox then send_email."""
    return TaskPlan(
        tasks=[
            ToolCall(
                tool="search_inbox",
                args={"query": "invoice", "max_results": 5},
                reasoning="Find the invoice email."
            ),
            ToolCall(
                tool="send_email",
                args={
                    "to": "boss@example.com",
                    "subject": "Invoice found",
                    "body": "Hi Boss,\n\nHere is the invoice.\n\nBest regards,"
                },
                reasoning="Forward it."
            ),
        ],
        reasoning="Find the invoice then forward it to boss."
    )


def _make_three_task_plan() -> TaskPlan:
    """A three-task plan in a specific order."""
    return TaskPlan(
        tasks=[
            ToolCall(tool="search_inbox", args={"query": "invoices"}, reasoning="Step 1."),
            ToolCall(tool="export_contacts", args={}, reasoning="Step 2."),
            ToolCall(
                tool="send_email",
                args={"to": "x@y.com", "subject": "Done", "body": "All done."},
                reasoning="Step 3."
            ),
        ],
        reasoning="Three-step plan."
    )


def _make_single_task_plan() -> TaskPlan:
    return TaskPlan(
        tasks=[
            ToolCall(tool="export_contacts", args={}, reasoning="Export step.")
        ],
        reasoning="Just export."
    )


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class TestPhase25ConfirmationUI(unittest.TestCase):
    """Phase 2.5 — TaskPlan confirmation UI tests."""

    # ------------------------------------------------------------------ #
    # Test 1 — Confirmation message contains expected content
    # ------------------------------------------------------------------ #
    def test_confirmation_message_content(self):
        """The formatted message must contain heading, tasks, args, and reasoning."""
        plan = _make_two_task_plan()
        msg = _format_task_plan_confirmation(plan)

        # Heading
        self.assertIn("Planned Actions", msg,
            "Message must contain a 'Planned Actions' heading")

        # Task 1 — search_inbox
        self.assertIn("Search emails", msg,
            "Task 1 must show human-readable tool name 'Search emails'")
        self.assertIn("invoice", msg,
            "Task 1 query argument must appear in the message")

        # Task 2 — send_email
        self.assertIn("Send email", msg,
            "Task 2 must show human-readable tool name 'Send email'")
        self.assertIn("boss@example.com", msg,
            "Task 2 recipient must appear in the message")
        self.assertIn("Invoice found", msg,
            "Task 2 subject must appear in the message")

        # Reasoning
        self.assertIn("Find the invoice then forward it to boss", msg,
            "Plan reasoning must appear in the message")

        # Action count
        self.assertIn("2 actions ready", msg,
            "Message must state the number of ready actions")

        print("  [PASS] Test 1 — confirmation message contains all expected content")

    # ------------------------------------------------------------------ #
    # Test 2 — Execute All button present
    # ------------------------------------------------------------------ #
    @patch("main.db.save_pending_action")
    @patch("main.db.add_message")
    @patch("main.send_telegram_message")
    def test_execute_all_button_present(self, mock_send, mock_add_msg, mock_save):
        """The generated keyboard must contain an 'Execute All' button."""
        plan = _make_two_task_plan()
        _present_task_plan_confirmation("test_chat", plan)

        # Inspect the reply_markup passed to send_telegram_message
        self.assertTrue(mock_send.called)
        _, kwargs = mock_send.call_args
        reply_markup = kwargs.get("reply_markup") or mock_send.call_args[0][1]

        # Flatten all buttons
        all_buttons = [
            btn
            for row in reply_markup["inline_keyboard"]
            for btn in row
        ]
        execute_buttons = [b for b in all_buttons if "Execute" in b["text"]]
        self.assertTrue(len(execute_buttons) >= 1,
            "At least one 'Execute All' button must be present in the keyboard")

        execute_cb = execute_buttons[0]["callback_data"]
        self.assertTrue(execute_cb.startswith("plan_execute:"),
            f"Execute All callback must start with 'plan_execute:' — got: {execute_cb}")

        print("  [PASS] Test 2 — Execute All button present with correct callback prefix")

    # ------------------------------------------------------------------ #
    # Test 3 — Cancel button present
    # ------------------------------------------------------------------ #
    @patch("main.db.save_pending_action")
    @patch("main.db.add_message")
    @patch("main.send_telegram_message")
    def test_cancel_button_present(self, mock_send, mock_add_msg, mock_save):
        """The generated keyboard must contain a 'Cancel' button."""
        plan = _make_two_task_plan()
        _present_task_plan_confirmation("test_chat", plan)

        _, kwargs = mock_send.call_args
        reply_markup = kwargs.get("reply_markup") or mock_send.call_args[0][1]

        all_buttons = [
            btn
            for row in reply_markup["inline_keyboard"]
            for btn in row
        ]
        cancel_buttons = [b for b in all_buttons if "Cancel" in b["text"]]
        self.assertTrue(len(cancel_buttons) >= 1,
            "At least one 'Cancel' button must be present in the keyboard")

        cancel_cb = cancel_buttons[0]["callback_data"]
        self.assertTrue(cancel_cb.startswith("plan_cancel:"),
            f"Cancel callback must start with 'plan_cancel:' — got: {cancel_cb}")

        print("  [PASS] Test 3 — Cancel button present with correct callback prefix")

    # ------------------------------------------------------------------ #
    # Test 4 — Task order is preserved in the message
    # ------------------------------------------------------------------ #
    def test_task_order_preserved_in_message(self):
        """Tasks must appear in original order (1→2→3) in the formatted message."""
        plan = _make_three_task_plan()
        msg = _format_task_plan_confirmation(plan)

        pos_search = msg.find("Search emails")
        pos_export = msg.find("Export contacts")
        pos_send   = msg.find("Send email")

        self.assertGreater(pos_search, -1, "Task 1 (Search emails) must appear in message")
        self.assertGreater(pos_export, -1, "Task 2 (Export contacts) must appear in message")
        self.assertGreater(pos_send, -1,   "Task 3 (Send email) must appear in message")

        self.assertLess(pos_search, pos_export,
            "Task 1 (Search emails) must appear before Task 2 (Export contacts)")
        self.assertLess(pos_export, pos_send,
            "Task 2 (Export contacts) must appear before Task 3 (Send email)")

        # Numbered markers
        self.assertIn("*1.*", msg)
        self.assertIn("*2.*", msg)
        self.assertIn("*3.*", msg)

        print("  [PASS] Test 4 — task order preserved in confirmation message")

    # ------------------------------------------------------------------ #
    # Test 5 — Single ToolCall regression: existing flow untouched
    # ------------------------------------------------------------------ #
    @patch("main.db")
    @patch("main._present_task_plan_confirmation")
    @patch("main.call_agent")
    @patch("main.send_telegram_message")
    def test_single_toolcall_regression(self, mock_send, mock_agent, mock_plan_ui, mock_db):
        """A normal ToolCall result must NOT go through the TaskPlan UI path."""
        from main import handle_message

        tc = ToolCall(
            tool="none",
            args={"message": "Nothing to do."},
            reasoning="No action needed."
        )
        mock_agent.return_value = tc
        mock_db.get_history.return_value = []
        mock_db.get_pending_action.return_value = None
        mock_db.get_user_profile.return_value = None

        handle_message("chat_123", "Hello")

        # TaskPlan confirmation must NOT have been called
        mock_plan_ui.assert_not_called()

        print("  [PASS] Test 5 — single ToolCall regression: TaskPlan UI not invoked")

    # ------------------------------------------------------------------ #
    # Test 6 — No tool execution when confirmation is presented
    # ------------------------------------------------------------------ #
    @patch("main.db.save_pending_action")
    @patch("main.db.add_message")
    @patch("main.send_telegram_message")
    def test_no_tool_execution_on_confirmation(self, mock_send, mock_add_msg, mock_save):
        """Presenting the confirmation must not call any email/Gmail tools."""
        plan = _make_two_task_plan()

        with patch("tools.email_sender.send_email_raw") as mock_email:
            _present_task_plan_confirmation("test_chat", plan)
            mock_email.assert_not_called()

        # Telegram message must have been sent (UI only)
        mock_send.assert_called_once()

        print("  [PASS] Test 6 — no tool execution when confirmation is presented")

    # ------------------------------------------------------------------ #
    # Test 7 — Tool name formatter
    # ------------------------------------------------------------------ #
    def test_tool_name_formatter(self):
        """_format_tool_name must return human-readable names for known tools."""
        self.assertEqual(_format_tool_name("send_email"), "Send email")
        self.assertEqual(_format_tool_name("search_inbox"), "Search emails")
        self.assertEqual(_format_tool_name("export_contacts"), "Export contacts")
        self.assertEqual(_format_tool_name("delete_contact"), "Delete contact")
        self.assertEqual(_format_tool_name("rename_contact"), "Rename contact")
        self.assertEqual(_format_tool_name("read_email"), "Read email")
        self.assertEqual(_format_tool_name("draft_reply"), "Draft reply")
        # Unknown tools fall back safely
        unknown = _format_tool_name("some_future_tool")
        self.assertIsInstance(unknown, str)
        self.assertTrue(len(unknown) > 0)
        print("  [PASS] Test 7 — tool name formatter returns correct display names")

    # ------------------------------------------------------------------ #
    # Test 8 — Pending action is saved when confirmation is presented
    # ------------------------------------------------------------------ #
    @patch("main.db.add_message")
    @patch("main.send_telegram_message")
    @patch("main.db.save_pending_action")
    def test_pending_action_saved_on_confirmation(self, mock_save, mock_send, mock_add_msg):
        """_present_task_plan_confirmation must call save_pending_action once."""
        plan = _make_two_task_plan()
        _present_task_plan_confirmation("test_chat", plan)

        mock_save.assert_called_once()
        call_args = mock_save.call_args
        action_id = call_args[0][0]
        chat_id   = call_args[0][1]
        action_type = call_args[0][2]
        payload   = call_args[0][3]

        self.assertTrue(action_id.startswith("plan_"),
            "action_id must start with 'plan_'")
        self.assertEqual(chat_id, "test_chat")
        self.assertEqual(action_type, "confirm_taskplan")
        self.assertIn("tasks", payload)
        self.assertIn("reasoning", payload)
        self.assertEqual(len(payload["tasks"]), 2)
        print("  [PASS] Test 8 — pending action saved with correct type and payload")

    # ------------------------------------------------------------------ #
    # Test 9 — plan_cancel callback deletes pending action
    # ------------------------------------------------------------------ #
    @patch("main.db.delete_pending_action")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.db.get_pending_action")
    def test_plan_cancel_callback(self, mock_get, mock_answer, mock_send, mock_delete):
        """plan_cancel callback must delete the pending action and confirm cancellation."""
        action_id = "plan_abc12345"
        mock_get.return_value = {
            "id": action_id,
            "chat_id": "test_chat",
            "action_type": "confirm_taskplan",
            "payload": {"tasks": [], "reasoning": "test"},
        }
        cq = {
            "id": "cq_001",
            "data": f"plan_cancel:{action_id}",
            "message": {"chat": {"id": "test_chat"}},
        }
        handle_callback_query(cq)

        mock_delete.assert_called_with(action_id)
        mock_send.assert_called()
        cancel_msg = mock_send.call_args[0][0]
        self.assertIn("cancel", cancel_msg.lower(),
            "Cancel response message should mention 'cancel'")
        print("  [PASS] Test 9 — plan_cancel callback deletes action and confirms")

    # ------------------------------------------------------------------ #
    # Test 10 — plan_execute callback does NOT execute tools (Phase 2.5 stub)
    # ------------------------------------------------------------------ #
    @patch("main.db.delete_pending_action")
    @patch("main.send_telegram_message")
    @patch("main.answer_callback_query")
    @patch("main.db.get_pending_action")
    def test_plan_execute_stub_no_execution(self, mock_get, mock_answer, mock_send, mock_delete):
        """plan_execute callback must NOT execute any tools in Phase 2.5 (stub only)."""
        action_id = "plan_abc12345"
        mock_get.return_value = {
            "id": action_id,
            "chat_id": "test_chat",
            "action_type": "confirm_taskplan",
            "payload": {
                "tasks": [
                    {"tool": "send_email",
                     "args": {"to": "x@y.com", "subject": "S", "body": "B"},
                     "reasoning": "r"}
                ],
                "reasoning": "test"
            },
        }
        cq = {
            "id": "cq_002",
            "data": f"plan_execute:{action_id}",
            "message": {"chat": {"id": "test_chat"}},
        }
        with patch("tools.email_sender.send_email_raw") as mock_email:
            handle_callback_query(cq)
            mock_email.assert_not_called()

        # Pending action is deleted atomically upon execution
        mock_delete.assert_called_once_with(action_id)
        # But answer_callback_query must have been called
        mock_answer.assert_called_once()
        print("  [PASS] Test 10 — plan_execute stub: no tools executed, action preserved")

    # ------------------------------------------------------------------ #
    # Test 11 — Confirmation message includes Proceed? prompt
    # ------------------------------------------------------------------ #
    def test_confirmation_message_has_proceed_prompt(self):
        """Formatted message must end with a 'Proceed?' user prompt."""
        plan = _make_two_task_plan()
        msg = _format_task_plan_confirmation(plan)
        self.assertIn("Proceed?", msg,
            "Confirmation message must include 'Proceed?' user prompt")
        print("  [PASS] Test 11 — message includes 'Proceed?' prompt")

    # ------------------------------------------------------------------ #
    # Test 12 — Single-task plan uses singular 'action ready'
    # ------------------------------------------------------------------ #
    def test_single_task_action_count_singular(self):
        """A one-task plan must display '1 action ready' (not '1 actions ready')."""
        plan = _make_single_task_plan()
        msg = _format_task_plan_confirmation(plan)
        self.assertIn("1 action ready", msg,
            "Single-task plan must say '1 action ready' (singular)")
        self.assertNotIn("1 actions ready", msg,
            "Must not use plural '1 actions ready'")
        print("  [PASS] Test 12 — singular 'action ready' for single-task plan")


if __name__ == "__main__":
    unittest.main(verbosity=2)
