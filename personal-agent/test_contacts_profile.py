import unittest
import os
import db.session as db
from agent.core import call_agent, validate_tool_call, _clean_email_body
from agent.schemas import ToolCall

class TestContactsAndUserProfile(unittest.TestCase):
    def setUp(self):
        self.chat_id = "test_contacts_user_123"
        # Clean up database state for test user before each test
        conn = db.get_db()
        with conn:
            conn.execute("DELETE FROM contacts WHERE chat_id = ?", (self.chat_id,))
            conn.execute("DELETE FROM user_profile WHERE chat_id = ?", (self.chat_id,))

    def tearDown(self):
        conn = db.get_db()
        with conn:
            conn.execute("DELETE FROM contacts WHERE chat_id = ?", (self.chat_id,))
            conn.execute("DELETE FROM user_profile WHERE chat_id = ?", (self.chat_id,))

    def test_a_first_mention_saves_contact(self):
        """(a) First mention with both name and email saves the contact to DB."""
        user_msg = "email Mr. Example example@gmail.com about tomorrow's schedule"
        # Invoke call_agent with chat_id to trigger auto-upsert
        call_agent(user_msg, chat_id=self.chat_id)
        
        contacts = db.get_contacts(self.chat_id)
        self.assertTrue(len(contacts) >= 1)
        c = contacts[0]
        self.assertEqual(c["email"], "example@gmail.com")
        self.assertEqual(c["normalized_name"], "example")

    def test_b_second_mention_auto_resolves(self):
        """(b) Second mention with just the name auto-resolves to saved email without asking."""
        # 1. Pre-save contact
        db.upsert_contact(self.chat_id, "Mr. Example", "example@gmail.com")
        
        # 2. Second prompt mentions only the name
        prompt = "send email to Mr. Example about tomorrow's shoot holiday"
        tool_call = call_agent(prompt, chat_id=self.chat_id)
        
        self.assertEqual(tool_call.tool, "send_email")
        self.assertEqual(tool_call.args.get("to"), "example@gmail.com")

    def test_c_fuzzy_typo_name_matches(self):
        """(c) A fuzzy/typo'd name still matches ('mr.exampl' matching 'Mr. Example')."""
        db.upsert_contact(self.chat_id, "Mr. Example", "example@gmail.com")
        
        # Exact find_contact_by_name test with typo
        matched = db.find_contact_by_name(self.chat_id, "mr.exampl")
        self.assertIsNotNone(matched)
        self.assertEqual(matched["email"], "example@gmail.com")
        
        # Check that unrelated name does NOT match
        unrelated = db.find_contact_by_name(self.chat_id, "Sarah")
        self.assertIsNone(unrelated)

    def test_d_user_profile_name_remembered_in_signoff(self):
        """(d) user_profile name is remembered and used in a sign-off without being told again."""
        # 1. User states name
        msg = "My name is Sade"
        call_agent(msg, chat_id=self.chat_id)
        
        # Verify saved in profile
        prof = db.get_user_profile(self.chat_id)
        self.assertIsNotNone(prof)
        self.assertEqual(prof["display_name"], "Sade")

        # 2. Test email body cleaning applies profile sender name
        raw_body = "Hi Sarandeep,\n\nTomorrow is a holiday for the movie shoot.\n\nBest regards,"
        cleaned_body = _clean_email_body(raw_body, sender_name=prof["display_name"])
        self.assertIn("Best regards,\nSade", cleaned_body)


if __name__ == "__main__":
    unittest.main()
