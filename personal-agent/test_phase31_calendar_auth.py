# personal-agent/test_phase31_calendar_auth.py
import unittest
from unittest.mock import patch, MagicMock
import os
import tempfile

from tools.google_auth import (
    get_google_credentials,
    get_calendar_service,
    get_gmail_service,
    _sanitize_auth_error,
    DEFAULT_SCOPES,
)
import config


class TestPhase31CalendarAuth(unittest.TestCase):
    """
    Focused unit tests for Phase 3.1 Google Calendar API authentication and setup.
    """

    def setUp(self):
        self.mock_creds = MagicMock()
        self.mock_creds.valid = True
        self.mock_creds.expired = False

    @patch("google.oauth2.credentials.Credentials.from_authorized_user_file")
    def test_gmail_auth_compatibility(self, mock_from_file):
        """1. Existing Gmail authentication remains compatible via get_gmail_service."""
        mock_from_file.return_value = self.mock_creds

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_tok:
            tmp_tok.write(b'{"token": "dummy_token"}')
            tmp_tok_path = tmp_tok.name

        try:
            with patch("googleapiclient.discovery.build") as mock_build:
                mock_service = MagicMock()
                mock_build.return_value = mock_service

                service = get_gmail_service(token_file=tmp_tok_path)

                mock_build.assert_called_once_with("gmail", "v1", credentials=self.mock_creds)
                self.assertEqual(service, mock_service)
        finally:
            if os.path.exists(tmp_tok_path):
                os.remove(tmp_tok_path)

    @patch("google.oauth2.credentials.Credentials.from_authorized_user_file")
    def test_calendar_service_construction(self, mock_from_file):
        """2. Calendar service can be constructed from the existing credential mechanism."""
        mock_from_file.return_value = self.mock_creds

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_tok:
            tmp_tok.write(b'{"token": "dummy_token"}')
            tmp_tok_path = tmp_tok.name

        try:
            with patch("googleapiclient.discovery.build") as mock_build:
                mock_service = MagicMock()
                mock_build.return_value = mock_service

                service = get_calendar_service(token_file=tmp_tok_path)

                mock_build.assert_called_once_with("calendar", "v3", credentials=self.mock_creds)
                self.assertEqual(service, mock_service)
        finally:
            if os.path.exists(tmp_tok_path):
                os.remove(tmp_tok_path)

    def test_missing_credentials_fails_safely(self):
        """3. Missing credentials fail safely without crashing with unhandled internal errors."""
        non_existent_token = "non_existent_token_file_9999.json"
        non_existent_cred = "non_existent_cred_file_9999.json"

        with self.assertRaises(FileNotFoundError) as ctx:
            get_google_credentials(
                credentials_file=non_existent_cred,
                token_file=non_existent_token,
            )

        self.assertIn("OAuth client credentials file", str(ctx.exception))

    def test_auth_error_sanitization(self):
        """4. Authentication errors are handled without exposing tokens/secrets."""
        secret_err = Exception("OAuth refresh token bearer_super_secret_token_12345 invalid")
        sanitized = _sanitize_auth_error(secret_err)

        self.assertNotIn("bearer_super_secret_token_12345", sanitized)
        self.assertNotIn("secret", sanitized)
        self.assertIn("Authentication error", sanitized)

    @patch("google.oauth2.credentials.Credentials.from_authorized_user_file")
    def test_no_duplicate_oauth_mechanism(self, mock_from_file):
        """5. No duplicate OAuth credential mechanism is introduced (Gmail and Calendar use same credentials)."""
        mock_from_file.return_value = self.mock_creds

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_tok:
            tmp_tok.write(b'{"token": "dummy_token"}')
            tmp_tok_path = tmp_tok.name

        try:
            creds1 = get_google_credentials(token_file=tmp_tok_path)
            creds2 = get_google_credentials(token_file=tmp_tok_path)

            self.assertEqual(creds1, creds2)
            # Verify calendar scope is present in default scopes
            self.assertIn("https://www.googleapis.com/auth/calendar.events", DEFAULT_SCOPES)
        finally:
            if os.path.exists(tmp_tok_path):
                os.remove(tmp_tok_path)


if __name__ == "__main__":
    unittest.main()
