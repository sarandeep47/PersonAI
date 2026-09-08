"""
Regression test: deleted-contact email must NOT resurface via conversation history.

Scenario
--------
1. Save contact  HR / hr@deleted-company.com  to the contacts table.
2. Inject a realistic conversation_history row that contains the email address
   (simulating a prior draft summary that was stored by _present_email_confirmation).
3. Delete the contact from the contacts table.
4. Call scrub_contact_from_history() — assert the email is gone from history.
5. Call call_agent() with "mail hr tomorrow I will be going to Himalayas"
   (name-only reference, no email in the message).
   The agent is patched so it returns send_email with the deleted address,
   which is the exact hallucination we are guarding against.
6. Assert the final ToolCall is tool="none" — NOT send_email.
7. Assert the deleted email address does NOT appear anywhere in the result.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Point the db module at a throw-away SQLite file for the duration of the test
# so we never touch the real agent_session.db.
# ---------------------------------------------------------------------------
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name

# Patch DB_PATH before any project imports load db.session
import importlib
import db.session as _db_mod
_db_mod.DB_PATH = _TMP_DB

import db.session as db
import agent.core as core
from agent.schemas import ToolCall

CHAT_ID = "test_regression_chat"
DELETED_EMAIL = "hr@deleted-company.com"
DELETED_NAME = "HR Shylaja"


def _make_send_email_json(to: str) -> str:
    """Return a raw JSON string that looks like an Ollama model response."""
    return json.dumps({
        "tool": "send_email",
        "args": {
            "to": to,
            "subject": "Leave Notice",
            "body": f"Dear HR,\n\nI will be going to Himalayas tomorrow.\n\nBest regards,\nTest"
        },
        "reasoning": "User asked to mail HR."
    })


class TestDeletedContactDoesNotLeak(unittest.TestCase):

    def setUp(self):
        """Fresh DB for every test method."""
        # Re-point and re-initialise the database
        _db_mod.DB_PATH = _TMP_DB
        db.DB_PATH = _TMP_DB
        db.init_db()

        # Wipe any rows left from a previous run
        conn = db.get_db()
        with conn:
            conn.execute("DELETE FROM contacts WHERE chat_id = ?", (CHAT_ID,))
            conn.execute("DELETE FROM conversation_history WHERE chat_id = ?", (CHAT_ID,))
            conn.execute("DELETE FROM pending_actions WHERE chat_id = ?", (CHAT_ID,))

    # ------------------------------------------------------------------
    # Helper: seed the DB as if a real user interaction happened
    # ------------------------------------------------------------------
    def _seed_history_with_deleted_email(self):
        """
        Step 1: save the contact.
        Step 2: add a history row that mentions the email (as _present_email_confirmation would).
        Step 3: delete the contact.
        Step 4: scrub history.
        Returns the number of scrubbed rows (must be >= 1).
        """
        # 1. Save contact
        db.upsert_contact(CHAT_ID, DELETED_NAME, DELETED_EMAIL)

        # 2. Simulate the assistant summary stored after a prior draft
        prior_summary = (
            f"Drafted email to {DELETED_EMAIL} with subject 'Test': "
            "Dear HR, I will be in touch. Best regards, Tester"
        )
        db.add_message(CHAT_ID, "user", "mail hr about my schedule")
        db.add_message(CHAT_ID, "assistant", prior_summary)

        # Confirm the email IS in history before deletion
        history_before = db.get_history(CHAT_ID)
        history_text = " ".join(m["content"] for m in history_before)
        self.assertIn(
            DELETED_EMAIL.lower(), history_text.lower(),
            "Pre-condition failed: deleted email should be present in history before scrub"
        )

        # 3. Delete the contact
        db.delete_contact(CHAT_ID, DELETED_NAME)
        self.assertIsNone(
            db.find_contact_by_name(CHAT_ID, "hr"),
            "Pre-condition failed: contact should be gone after delete"
        )

        # 4. Scrub history
        scrubbed = db.scrub_contact_from_history(CHAT_ID, DELETED_EMAIL)
        return scrubbed

    # ------------------------------------------------------------------
    # Test A: scrub_contact_from_history actually redacts the email
    # ------------------------------------------------------------------
    def test_scrub_removes_email_from_history(self):
        scrubbed = self._seed_history_with_deleted_email()

        self.assertGreaterEqual(scrubbed, 1, "scrub_contact_from_history should report >= 1 modified row")

        history_after = db.get_history(CHAT_ID)
        history_text = " ".join(m["content"] for m in history_after)

        self.assertNotIn(
            DELETED_EMAIL.lower(), history_text.lower(),
            f"Deleted email '{DELETED_EMAIL}' must NOT appear in history after scrub"
        )
        self.assertIn(
            "[redacted]", history_text,
            "History should contain '[redacted]' placeholder after scrub"
        )

    # ------------------------------------------------------------------
    # Test B: validate_tool_call blocks send_email to deleted address
    #         even when it is present in history (two-tier trust model)
    # ------------------------------------------------------------------
    def test_validate_blocks_email_from_history_only(self):
        self._seed_history_with_deleted_email()

        # History still contains [redacted], but let's also test the
        # pre-scrub scenario: manually inject the raw email back into history
        # to directly exercise validate_tool_call's trust boundary.
        db.add_message(CHAT_ID, "assistant",
                       f"Drafted email to {DELETED_EMAIL} with subject 'Old draft'.")

        history = db.get_history(CHAT_ID, limit=6)

        # Construct a tool call as if the model hallucinated the deleted address
        hallucinated = ToolCall(
            tool="send_email",
            args={"to": DELETED_EMAIL, "subject": "Leave", "body": "Hi"},
            reasoning="model recalled deleted email from history"
        )

        # User message has NO email in it — only a name reference
        user_msg = "mail hr tomorrow I will be going to Himalayas"

        result = core.validate_tool_call(
            hallucinated,
            user_message=user_msg,
            history=history,
            chat_id=CHAT_ID
        )

        self.assertEqual(
            result.tool, "none",
            f"validate_tool_call must block send_email to '{DELETED_EMAIL}' "
            f"when it appears only in history, not in the current message or live contacts. "
            f"Got tool='{result.tool}' instead."
        )
        result_str = json.dumps(result.args)
        self.assertNotIn(
            DELETED_EMAIL.lower(), result_str.lower(),
            f"Deleted email '{DELETED_EMAIL}' must NOT appear in the validated result args"
        )

    # ------------------------------------------------------------------
    # Test C: full call_agent() end-to-end — model returns deleted email,
    #         the pipeline must intercept it and return tool="none"
    # ------------------------------------------------------------------
    def test_call_agent_does_not_resolve_deleted_contact_email(self):
        self._seed_history_with_deleted_email()

        # Load the post-scrub history (the scrubbed rows contain [redacted])
        history = db.get_history(CHAT_ID, limit=6)

        user_msg = "mail hr tomorrow I will be going to Himalayas"

        # Patch the LLM to return the worst-case hallucination:
        # the model "remembers" the deleted email from its context window
        # and places it directly in args.to.
        with patch("agent.core._ollama_chat") as mock_ollama:
            mock_ollama.return_value = _make_send_email_json(DELETED_EMAIL)

            result = core.call_agent(user_msg, history=history, chat_id=CHAT_ID)

        # The pipeline must NOT let send_email through to the deleted address
        self.assertEqual(
            result.tool, "none",
            f"call_agent must return tool='none' when the model hallucinated "
            f"the deleted email '{DELETED_EMAIL}'. Got tool='{result.tool}'."
        )

        # The deleted email must not appear anywhere in the response
        result_str = json.dumps({"tool": result.tool, "args": result.args})
        self.assertNotIn(
            DELETED_EMAIL.lower(), result_str.lower(),
            f"Deleted email '{DELETED_EMAIL}' must NOT appear anywhere in call_agent result. "
            f"Got: {result_str}"
        )

        print(
            f"\n[PASS] call_agent correctly returned tool='{result.tool}' "
            f"with message: {result.args.get('message', '')}"
        )

    # ------------------------------------------------------------------
    # Test D: call_agent() still works for a LIVE contact after deletion
    #         of a different contact (no false positives)
    # ------------------------------------------------------------------
    def test_live_contact_still_resolves_after_other_deletion(self):
        self._seed_history_with_deleted_email()

        # Add a DIFFERENT, still-live contact
        live_email = "alice@live-company.com"
        db.upsert_contact(CHAT_ID, "Alice Manager", live_email)

        history = db.get_history(CHAT_ID, limit=6)
        user_msg = "mail Alice tomorrow I will be on leave"

        with patch("agent.core._ollama_chat") as mock_ollama:
            # Model correctly resolves live contact
            mock_ollama.return_value = _make_send_email_json(live_email)

            result = core.call_agent(user_msg, history=history, chat_id=CHAT_ID)

        self.assertEqual(
            result.tool, "send_email",
            f"call_agent must return tool='send_email' for a live contact '{live_email}'. "
            f"Got tool='{result.tool}'."
        )
        self.assertEqual(
            result.args.get("to", "").lower(), live_email.lower(),
            f"send_email 'to' must be the live contact email '{live_email}'. "
            f"Got: {result.args.get('to')}"
        )
        print(
            f"\n[PASS] Live contact correctly resolved: tool='{result.tool}' "
            f"to='{result.args.get('to')}'"
        )


if __name__ == "__main__":
    # Clean up temp db on exit
    try:
        unittest.main(verbosity=2)
    finally:
        try:
            os.unlink(_TMP_DB)
        except OSError:
            pass
