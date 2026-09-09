# personal-agent/test_phase33_calendar_schemas.py
import unittest
from pydantic import ValidationError

from agent.schemas import (
    ScheduleCalendarArgs,
    ListCalendarArgs,
    ToolCall,
    ToolName,
    TOOL_ARGS_SCHEMAS,
)
from agent.core import validate_tool_call


class TestPhase33CalendarSchemas(unittest.TestCase):
    """Focused unit tests for Phase 3.3 Calendar Pydantic Schemas & ToolName integration."""

    # ------------------------------------------------------------------ #
    # ScheduleCalendarArgs Tests
    # ------------------------------------------------------------------ #

    def test_schedule_calendar_args_valid(self):
        """Verify valid ScheduleCalendarArgs constructs properly."""
        args = ScheduleCalendarArgs(
            title="Team Planning",
            date="2026-10-20",
            start_time="14:30",
            duration_minutes=45,
            attendees=["dev@example.com", "lead@example.com"],
        )
        self.assertEqual(args.title, "Team Planning")
        self.assertEqual(args.date, "2026-10-20")
        self.assertEqual(args.start_time, "14:30")
        self.assertEqual(args.duration_minutes, 45)
        self.assertEqual(args.attendees, ["dev@example.com", "lead@example.com"])

    def test_schedule_calendar_args_without_optional_attendees(self):
        """Verify ScheduleCalendarArgs default attendees is None."""
        args = ScheduleCalendarArgs(
            title="Solo Focus",
            date="2026-10-20",
            start_time="10:00 AM",
            duration_minutes=30,
        )
        self.assertEqual(args.title, "Solo Focus")
        self.assertIsNone(args.attendees)

    def test_schedule_calendar_args_missing_required_fields(self):
        """Verify missing required fields raise ValidationError."""
        with self.assertRaises(ValidationError):
            ScheduleCalendarArgs(
                date="2026-10-20",
                start_time="14:00",
                duration_minutes=30,
            )

    def test_schedule_calendar_args_empty_title(self):
        """Verify empty title is rejected."""
        with self.assertRaises(ValidationError):
            ScheduleCalendarArgs(
                title="   ",
                date="2026-10-20",
                start_time="14:00",
                duration_minutes=30,
            )

    def test_schedule_calendar_args_invalid_date(self):
        """Verify invalid date format raises ValidationError."""
        with self.assertRaises(ValidationError):
            ScheduleCalendarArgs(
                title="Meeting",
                date="20-10-2026",  # wrong format
                start_time="14:00",
                duration_minutes=30,
            )

    def test_schedule_calendar_args_invalid_time(self):
        """Verify invalid start_time format raises ValidationError."""
        with self.assertRaises(ValidationError):
            ScheduleCalendarArgs(
                title="Meeting",
                date="2026-10-20",
                start_time="25:99",  # invalid time
                duration_minutes=30,
            )

    def test_schedule_calendar_args_non_positive_duration(self):
        """Verify duration_minutes <= 0 raises ValidationError."""
        with self.assertRaises(ValidationError):
            ScheduleCalendarArgs(
                title="Meeting",
                date="2026-10-20",
                start_time="14:00",
                duration_minutes=0,
            )
        with self.assertRaises(ValidationError):
            ScheduleCalendarArgs(
                title="Meeting",
                date="2026-10-20",
                start_time="14:00",
                duration_minutes=-15,
            )

    def test_schedule_calendar_args_invalid_attendee_email(self):
        """Verify invalid attendee email raises ValidationError."""
        with self.assertRaises(ValidationError):
            ScheduleCalendarArgs(
                title="Meeting",
                date="2026-10-20",
                start_time="14:00",
                duration_minutes=30,
                attendees=["not_an_email"],
            )

    # ------------------------------------------------------------------ #
    # ListCalendarArgs Tests
    # ------------------------------------------------------------------ #

    def test_list_calendar_args_valid_window(self):
        """Verify valid ListCalendarArgs window parsing."""
        args = ListCalendarArgs(
            start_datetime="2026-10-20T00:00:00+05:30",
            end_datetime="2026-10-20T23:59:59+05:30",
        )
        self.assertEqual(args.start_datetime, "2026-10-20T00:00:00+05:30")
        self.assertEqual(args.end_datetime, "2026-10-20T23:59:59+05:30")

    def test_list_calendar_args_empty_optional(self):
        """Verify ListCalendarArgs can be constructed without arguments."""
        args = ListCalendarArgs()
        self.assertIsNone(args.start_datetime)
        self.assertIsNone(args.end_datetime)

    def test_list_calendar_args_invalid_datetime(self):
        """Verify invalid datetime string raises ValidationError."""
        with self.assertRaises(ValidationError):
            ListCalendarArgs(start_datetime="invalid-datetime-str")

    # ------------------------------------------------------------------ #
    # ToolName Enum Tests
    # ------------------------------------------------------------------ #

    def test_tool_name_literals(self):
        """Verify schedule_calendar and list_calendar are accepted ToolName literals."""
        tc_schedule = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "Sync",
                "date": "2026-10-20",
                "start_time": "14:00",
                "duration_minutes": 30,
            },
            reasoning="Valid schedule_calendar tool call",
        )
        self.assertEqual(tc_schedule.tool, "schedule_calendar")

        tc_list = ToolCall(
            tool="list_calendar",
            args={},
            reasoning="Valid list_calendar tool call",
        )
        self.assertEqual(tc_list.tool, "list_calendar")

        # Confirm mapping table includes new tool names
        self.assertIn("schedule_calendar", TOOL_ARGS_SCHEMAS)
        self.assertIn("list_calendar", TOOL_ARGS_SCHEMAS)
        self.assertEqual(TOOL_ARGS_SCHEMAS["schedule_calendar"], ScheduleCalendarArgs)
        self.assertEqual(TOOL_ARGS_SCHEMAS["list_calendar"], ListCalendarArgs)

    # ------------------------------------------------------------------ #
    # ToolCall Validation Integration Tests
    # ------------------------------------------------------------------ #

    def test_validate_tool_call_valid_schedule_calendar(self):
        """Verify valid schedule_calendar ToolCall passes validate_tool_call."""
        tc = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "Design Sprint",
                "date": "2026-10-20",
                "start_time": "15:00",
                "duration_minutes": 60,
                "attendees": ["alex@example.com"],
            },
            reasoning="Schedule design sprint",
        )
        res = validate_tool_call(tc, "Schedule a design sprint on Oct 20 at 3pm with alex@example.com")
        self.assertEqual(res.tool, "schedule_calendar")
        self.assertEqual(res.args["title"], "Design Sprint")

    def test_validate_tool_call_invalid_schedule_calendar_overridden(self):
        """Verify malformed schedule_calendar ToolCall is rejected and overridden to none."""
        tc = ToolCall(
            tool="schedule_calendar",
            args={
                "title": "",  # invalid empty title
                "date": "invalid-date",
                "start_time": "15:00",
                "duration_minutes": -10,  # invalid negative duration
            },
            reasoning="Malformed schedule_calendar tool call",
        )
        res = validate_tool_call(tc, "Schedule meeting")
        self.assertEqual(res.tool, "none")
        self.assertIn("Invalid arguments", res.args.get("message", ""))

    def test_validate_tool_call_valid_list_calendar(self):
        """Verify valid list_calendar ToolCall passes validate_tool_call."""
        tc = ToolCall(
            tool="list_calendar",
            args={"start_datetime": "2026-10-20"},
            reasoning="List events for tomorrow",
        )
        res = validate_tool_call(tc, "What's on my calendar tomorrow?")
        self.assertEqual(res.tool, "list_calendar")


if __name__ == "__main__":
    unittest.main()
