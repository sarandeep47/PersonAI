# personal-agent/test_phase32_calendar_tools.py
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from tools.calendar import create_event, list_upcoming_events, _sanitize_error


class TestPhase32CalendarTools(unittest.TestCase):
    """Focused unit tests for Phase 3.2 Google Calendar tools."""

    def setUp(self):
        self.mock_service = MagicMock()
        self.mock_events = MagicMock()
        self.mock_service.events.return_value = self.mock_events

    # ------------------------------------------------------------------ #
    # Tests for create_event()
    # ------------------------------------------------------------------ #

    def test_create_event_success(self):
        """test_create_event: verifies primary calendar, body construction, duration math, and attendee format."""
        mock_insert = MagicMock()
        self.mock_events.insert.return_value = mock_insert
        mock_insert.execute.return_value = {
            "id": "event_12345",
            "summary": "Project Sync",
            "start": {"dateTime": "2026-10-15T10:00:00+05:30"},
            "end": {"dateTime": "2026-10-15T11:00:00+05:30"},
            "attendees": [{"email": "alice@example.com"}, {"email": "bob@example.com"}],
            "htmlLink": "https://calendar.google.com/event?id=12345",
        }

        result = create_event(
            title="Project Sync",
            date="2026-10-15",
            start_time="10:00",
            duration_minutes=60,
            attendees=["alice@example.com", "bob@example.com"],
            service=self.mock_service,
        )

        # Assert insert called with calendarId="primary"
        self.mock_events.insert.assert_called_once()
        call_kwargs = self.mock_events.insert.call_args[1]
        self.assertEqual(call_kwargs.get("calendarId"), "primary")

        body = call_kwargs.get("body", {})
        self.assertEqual(body.get("summary"), "Project Sync")
        self.assertIn("2026-10-15T10:00:00", body.get("start", {}).get("dateTime"))
        self.assertIn("2026-10-15T11:00:00", body.get("end", {}).get("dateTime"))
        self.assertEqual(
            body.get("attendees"),
            [{"email": "alice@example.com"}, {"email": "bob@example.com"}],
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["id"], "event_12345")
        self.assertEqual(result["title"], "Project Sync")
        self.assertEqual(result["attendees"], ["alice@example.com", "bob@example.com"])

    def test_create_event_without_attendees(self):
        """test_create_event: optional attendees default to None/empty."""
        mock_insert = MagicMock()
        self.mock_events.insert.return_value = mock_insert
        mock_insert.execute.return_value = {
            "id": "event_67890",
            "summary": "Solo Focus Block",
            "start": {"dateTime": "2026-10-15T14:00:00+05:30"},
            "end": {"dateTime": "2026-10-15T14:30:00+05:30"},
        }

        result = create_event(
            title="Solo Focus Block",
            date="2026-10-15",
            start_time="14:00",
            duration_minutes=30,
            service=self.mock_service,
        )

        body = self.mock_events.insert.call_args[1].get("body", {})
        self.assertNotIn("attendees", body)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["attendees"], [])

    def test_create_event_error_handling(self):
        """test_create_event: API errors are caught safely without exposing secrets/tokens."""
        mock_insert = MagicMock()
        self.mock_events.insert.return_value = mock_insert
        mock_insert.execute.side_effect = Exception("Google HTTP 403: bearer_token_secret_123 forbidden")

        result = create_event(
            title="Test Event",
            date="2026-10-15",
            start_time="10:00",
            duration_minutes=30,
            service=self.mock_service,
        )

        self.assertEqual(result["status"], "error")
        self.assertNotIn("bearer_token_secret_123", result["message"])
        self.assertIn("Calendar API error", result["message"])

    # ------------------------------------------------------------------ #
    # Tests for list_upcoming_events()
    # ------------------------------------------------------------------ #

    def test_list_upcoming_events_success(self):
        """test_list_upcoming_events: verifies timeMin, timeMax, singleEvents, orderBy, and concise output format."""
        mock_list = MagicMock()
        self.mock_events.list.return_value = mock_list
        mock_list.execute.return_value = {
            "items": [
                {
                    "id": "evt_1",
                    "summary": "Morning Standup",
                    "start": {"dateTime": "2026-10-16T09:00:00+05:30"},
                    "end": {"dateTime": "2026-10-16T09:30:00+05:30"},
                    "attendees": [{"email": "team@example.com"}],
                },
                {
                    "id": "evt_2",
                    "summary": "Design Sync",
                    "start": {"dateTime": "2026-10-16T11:00:00+05:30"},
                    "end": {"dateTime": "2026-10-16T12:00:00+05:30"},
                    "attendees": [],
                },
            ]
        }

        start_win = "2026-10-16T00:00:00+05:30"
        end_win = "2026-10-16T23:59:59+05:30"

        events = list_upcoming_events(
            start_datetime=start_win,
            end_datetime=end_win,
            service=self.mock_service,
        )

        self.mock_events.list.assert_called_once()
        call_kwargs = self.mock_events.list.call_args[1]
        self.assertEqual(call_kwargs.get("calendarId"), "primary")
        self.assertEqual(call_kwargs.get("singleEvents"), True)
        self.assertEqual(call_kwargs.get("orderBy"), "startTime")
        self.assertEqual(call_kwargs.get("timeMin"), start_win)
        self.assertEqual(call_kwargs.get("timeMax"), end_win)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["id"], "evt_1")
        self.assertEqual(events[0]["title"], "Morning Standup")
        self.assertEqual(events[0]["attendees"], ["team@example.com"])
        self.assertEqual(events[1]["id"], "evt_2")
        self.assertEqual(events[1]["title"], "Design Sync")

    def test_list_upcoming_events_all_day(self):
        """test_list_upcoming_events: correctly formats all-day events using start.date."""
        mock_list = MagicMock()
        self.mock_events.list.return_value = mock_list
        mock_list.execute.return_value = {
            "items": [
                {
                    "id": "allday_1",
                    "summary": "Company Holiday",
                    "start": {"date": "2026-10-25"},
                    "end": {"date": "2026-10-26"},
                }
            ]
        }

        events = list_upcoming_events(service=self.mock_service)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["id"], "allday_1")
        self.assertEqual(events[0]["title"], "Company Holiday")
        self.assertEqual(events[0]["start"], "2026-10-25")
        self.assertEqual(events[0]["end"], "2026-10-26")

    def test_list_upcoming_events_empty_and_error(self):
        """test_list_upcoming_events: handles empty calendar and API errors safely."""
        mock_list = MagicMock()
        self.mock_events.list.return_value = mock_list

        # Empty calendar
        mock_list.execute.return_value = {"items": []}
        empty_res = list_upcoming_events(service=self.mock_service)
        self.assertEqual(empty_res, [])

        # API error
        mock_list.execute.side_effect = Exception("OAuth token bearer_secret_999 expired")
        err_res = list_upcoming_events(service=self.mock_service)
        self.assertEqual(err_res, [])


if __name__ == "__main__":
    unittest.main()
