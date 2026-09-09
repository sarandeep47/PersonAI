import unittest
import os
import csv
import db.session as db
from agent.core import call_agent

class TestContactFeatures(unittest.TestCase):
    def setUp(self):
        self.chat_id = "test_feature_chat_456"
        db.init_db()
        conn = db.get_db()
        with conn:
            conn.execute("DELETE FROM contacts WHERE chat_id = ?", (self.chat_id,))
            conn.execute("DELETE FROM pending_actions WHERE chat_id = ?", (self.chat_id,))

    def tearDown(self):
        conn = db.get_db()
        with conn:
            conn.execute("DELETE FROM contacts WHERE chat_id = ?", (self.chat_id,))
            conn.execute("DELETE FROM pending_actions WHERE chat_id = ?", (self.chat_id,))

    def test_export_contacts_csv(self):
        """Verify CSV spreadsheet export file generation with proper headers and data."""
        db.upsert_contact(self.chat_id, "Old HR", "old_hr@company.com")
        db.upsert_contact(self.chat_id, "New Manager", "manager@newcorp.com")

        csv_path = db.export_contacts_csv(self.chat_id)
        self.assertIsNotNone(csv_path)
        self.assertTrue(os.path.exists(csv_path))

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))
            self.assertEqual(reader[0], ["Name", "Email", "Date Added", "Last Used"])
            names = [row[0] for row in reader[1:]]
            self.assertIn("Old HR", names)
            self.assertIn("New Manager", names)

    def test_export_contacts_tool_classification(self):
        """(Real LLM path) 'get me the database of the mail contacts' resolves to export_contacts tool."""
        user_msg = "get me the database of the mail contacts"
        tool_call = call_agent(user_msg, chat_id=self.chat_id)
        self.assertEqual(tool_call.tool, "export_contacts")

    def test_export_contacts_variations(self):
        """Verify export database triggers resolve to export_contacts."""
        prompts = [
            "get me database of my contact", "i need my database", "export contacts",
            "get me the database", "get my the database", "get me csv link", "csv link"
        ]
        for prompt in prompts:
            tool_call = call_agent(prompt, chat_id=self.chat_id)
            self.assertEqual(tool_call.tool, "export_contacts", f"Failed for prompt: '{prompt}'")

    def test_delete_contact_variations(self):
        """Verify delete triggers (delete the hr, delete hr data, delete the hr data) resolve to delete_contact."""
        db.upsert_contact(self.chat_id, "HR Shylaja", "statsmaster.12.5@gmail.com")
        prompts = ["delete the hr data", "delete the hr", "delete hr"]
        for prompt in prompts:
            tool_call = call_agent(prompt, chat_id=self.chat_id)
            self.assertEqual(tool_call.tool, "delete_contact", f"Failed tool for prompt: '{prompt}'")
            self.assertEqual(tool_call.args.get("query", "").lower(), "hr", f"Failed query for prompt: '{prompt}'")

    def test_delete_contact_tool_classification_single(self):
        """(Real LLM path) 'can you delete the hr data' resolves to delete_contact(query='hr') and single contact resolution."""
        db.upsert_contact(self.chat_id, "HR Shylaja", "statsmaster.12.5@gmail.com")

        user_msg = "can you delete the hr data"
        tool_call = call_agent(user_msg, chat_id=self.chat_id)
        self.assertEqual(tool_call.tool, "delete_contact")
        self.assertIn("hr", tool_call.args.get("query", "").lower())

        matches = db.find_contacts_matching_query(self.chat_id, tool_call.args.get("query"))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["name"], "HR Shylaja")
        self.assertEqual(matches[0]["email"], "statsmaster.12.5@gmail.com")

    def test_ambiguous_delete_contact_query_blocks(self):
        """Ambiguous deletion query matching multiple contacts blocks auto-deletion."""
        db.upsert_contact(self.chat_id, "HR Shylaja", "statsmaster.12.5@gmail.com")
        db.upsert_contact(self.chat_id, "HR Manager", "hr@corp.com")

        user_msg = "can you delete the hr data"
        tool_call = call_agent(user_msg, chat_id=self.chat_id)
        self.assertEqual(tool_call.tool, "delete_contact")

        query = tool_call.args.get("query", "hr")
        matches = db.find_contacts_matching_query(self.chat_id, query)
        # Should return BOTH contacts, triggering ambiguity blocking
        self.assertTrue(len(matches) > 1)
        matched_emails = [m["email"] for m in matches]
        self.assertIn("statsmaster.12.5@gmail.com", matched_emails)
        self.assertIn("hr@corp.com", matched_emails)


if __name__ == "__main__":
    unittest.main()
