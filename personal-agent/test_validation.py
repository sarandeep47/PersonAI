import unittest
from agent.schemas import ToolCall
from agent.core import validate_tool_call, revise_draft

class TestToolCallValidation(unittest.TestCase):
    def test_send_email_real_address_passes(self):
        """(a) A message with a real email address that should pass through."""
        user_msg = "Send an email to sarah@example.com about the project status update"
        initial_tool_call = ToolCall(
            tool="send_email",
            args={
                "to": "sarah@example.com",
                "subject": "Project Status Update",
                "body": "Hi Sarah, here is the update."
            },
            reasoning="Valid email address provided in input."
        )
        validated = validate_tool_call(initial_tool_call, user_msg)
        self.assertEqual(validated.tool, "send_email")
        self.assertEqual(validated.args.get("to"), "sarah@example.com")

    def test_send_email_hallucinated_address_blocked(self):
        """(b) A vague request like 'email my boss' that should get blocked and overridden to 'none'."""
        user_msg = "Send an email to my boss"
        initial_tool_call = ToolCall(
            tool="send_email",
            args={
                "to": "boss@domain.com",
                "subject": "Update",
                "body": "Hi Boss, update for you."
            },
            reasoning="Hallucinated recipient address."
        )
        validated = validate_tool_call(initial_tool_call, user_msg)
        self.assertEqual(validated.tool, "none")
        self.assertIn("message", validated.args)
        self.assertIn("email address", validated.args["message"].lower())

    def test_draft_reply_real_email_id_passes(self):
        """Draft reply with email_id present in original input message passes through."""
        user_msg = "Draft a reply to email ID msg101 saying I agree with the proposal"
        initial_tool_call = ToolCall(
            tool="draft_reply",
            args={
                "email_id": "msg101",
                "instructions": "I agree with the proposal"
            },
            reasoning="Valid email_id provided in prompt."
        )
        validated = validate_tool_call(initial_tool_call, user_msg)
        self.assertEqual(validated.tool, "draft_reply")
        self.assertEqual(validated.args.get("email_id"), "msg101")

    def test_draft_reply_hallucinated_email_id_blocked(self):
        """Draft reply with hallucinated email_id not in original message gets blocked."""
        user_msg = "Reply to the latest email telling them I am away"
        initial_tool_call = ToolCall(
            tool="draft_reply",
            args={
                "email_id": "msg9999",
                "instructions": "I am away"
            },
            reasoning="Hallucinated email_id."
        )
        validated = validate_tool_call(initial_tool_call, user_msg)
        self.assertEqual(validated.tool, "none")
        self.assertIn("message", validated.args)

    def test_three_sequential_revisions_no_note(self):
        """Perform 3 sequential revisions in a row on the same draft and assert no Note: or raw instruction text is appended."""
        initial_draft = {
            "to": "sarandeep@example.com",
            "subject": "Meeting Follow-up",
            "body": "Dear All,\n\nHere are the meeting notes from today.\n\nBest regards,\nTeam"
        }
        
        # Revision 1
        instruction_1 = "make it just 'Hi Sarandeep' instead of 'Dear All'"
        revised_1 = revise_draft(initial_draft, instruction_1)
        self.assertEqual(revised_1.tool, "send_email")
        self.assertNotIn("Note:", revised_1.args["body"])
        self.assertNotIn(instruction_1, revised_1.args["body"])

        # Revision 2
        draft_state_1 = revised_1.args
        instruction_2 = "with regards, Sade — enough, not more than that"
        revised_2 = revise_draft(draft_state_1, instruction_2)
        self.assertEqual(revised_2.tool, "send_email")
        self.assertNotIn("Note:", revised_2.args["body"])
        self.assertNotIn(instruction_2, revised_2.args["body"])

        # Revision 3
        draft_state_2 = revised_2.args
        instruction_3 = "change subject to Quick Sync"
        revised_3 = revise_draft(draft_state_2, instruction_3)
        self.assertEqual(revised_3.tool, "send_email")
        self.assertNotIn("Note:", revised_3.args["body"])
        self.assertNotIn(instruction_3, revised_3.args["body"])

    def test_has_placeholder_detection(self):
        """Verify _has_placeholder correctly detects bracketed template tags in drafts."""
        from agent.core import _has_placeholder
        
        draft_with_placeholder = ToolCall(
            tool="send_email",
            args={"to": "test@example.com", "subject": "Test", "body": "Hi, [insert contact info here]."},
            reasoning="Testing"
        )
        self.assertTrue(_has_placeholder(draft_with_placeholder))

        clean_draft = ToolCall(
            tool="send_email",
            args={"to": "test@example.com", "subject": "Test", "body": "Hi, please call me tomorrow."},
            reasoning="Testing"
        )
        self.assertFalse(_has_placeholder(clean_draft))

    def test_missing_reasoning_field_defaults(self):
        """Verify ToolCall validates JSON correctly when reasoning field is omitted by LLM."""
        json_str = '{"tool": "send_email", "args": {"to": "statsmaster.12.5@gmail.com", "subject": "Resume", "body": "Hi"}}'
        tc = ToolCall.model_validate_json(json_str)
        self.assertEqual(tc.tool, "send_email")
        self.assertEqual(tc.reasoning, "No reasoning provided.")

    def test_validate_tool_call_history_context(self):
        """Verify validate_tool_call checks history context for recipient email address."""
        history = [
            {"role": "user", "content": "email sarandeep8355@gmail.com about tomorrow's holiday"},
            {"role": "assistant", "content": "Drafted email to sarandeep8355@gmail.com with subject 'Holiday'"}
        ]
        followup_msg = "use Sade my name and attach photo"
        tc = ToolCall(
            tool="send_email",
            args={"to": "sarandeep8355@gmail.com", "subject": "Holiday", "body": "Hello Sarandeep"},
            reasoning="Valid recipient in history context."
        )
        validated = validate_tool_call(tc, followup_msg, history=history)
        self.assertEqual(validated.tool, "send_email")
        self.assertEqual(validated.args.get("to"), "sarandeep8355@gmail.com")

    def test_get_latest_pending_draft(self):
        """Verify db.get_latest_pending_draft retrieves draft payload."""
        import db.session as db
        chat_id = "test_chat_99"
        payload = {"to": "test@example.com", "subject": "Test", "body": "Body"}
        db.save_pending_action("draft_test123", chat_id, "confirm_send", payload)
        
        draft = db.get_latest_pending_draft(chat_id)
        self.assertIsNotNone(draft)
        self.assertEqual(draft["to"], "test@example.com")
        db.delete_pending_action("draft_test123")


if __name__ == "__main__":
    unittest.main()
