# personal-agent/tools/calendar.py
import logging
from datetime import datetime, timedelta
from typing import Optional, Any, List, Dict, Union
from tools.google_auth import get_calendar_service

logger = logging.getLogger(__name__)


def _sanitize_error(err: Exception) -> str:
    """Sanitize error message to prevent leaking credentials or raw tokens."""
    if not err:
        return "Unknown error"
    return "Calendar API error: Unable to complete requested calendar operation."


def _parse_datetime(date_str: str, time_str: str) -> datetime:
    """
    Parse date and start_time strings into a timezone-aware datetime object.
    Supports YYYY-MM-DD for date and various time formats (24h or 12h AM/PM).
    Defaults to local system timezone if naive.
    """
    clean_date = date_str.strip()
    clean_time = time_str.strip()

    dt = None
    time_formats = ["%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"]
    date_formats = ["%Y-%m-%d"]

    for df in date_formats:
        for tf in time_formats:
            try:
                dt = datetime.strptime(f"{clean_date} {clean_time}", f"{df} {tf}")
                break
            except ValueError:
                continue
        if dt:
            break

    if dt is None:
        try:
            dt = datetime.fromisoformat(f"{clean_date}T{clean_time}")
        except ValueError as e:
            raise ValueError(f"Could not parse date '{date_str}' or time '{time_str}'") from e

    if dt.tzinfo is None:
        dt = dt.astimezone()

    return dt


def create_event(
    title: str,
    date: str,
    start_time: str,
    duration_minutes: int,
    attendees: Optional[List[str]] = None,
    service: Any = None,
) -> Dict[str, Any]:
    """
    Create a Google Calendar event on the primary calendar.

    Args:
        title: Title/summary of the event.
        date: Date string (YYYY-MM-DD).
        start_time: Start time string (e.g. "14:00" or "02:00 PM").
        duration_minutes: Duration of event in minutes.
        attendees: Optional list of attendee email strings.
        service: Optional pre-constructed Calendar API service instance.

    Returns:
        Structured dict representing created event or error status.
    """
    try:
        if service is None:
            service = get_calendar_service()

        start_dt = _parse_datetime(date, start_time)
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        event_body = {
            "summary": title,
            "start": {
                "dateTime": start_dt.isoformat(),
            },
            "end": {
                "dateTime": end_dt.isoformat(),
            },
        }

        if attendees:
            event_body["attendees"] = [
                {"email": email.strip()} for email in attendees if isinstance(email, str) and email.strip()
            ]

        created = (
            service.events()
            .insert(calendarId="primary", body=event_body)
            .execute()
        )

        attendee_emails = [
            a.get("email")
            for a in created.get("attendees", [])
            if isinstance(a, dict) and "email" in a
        ]

        return {
            "status": "success",
            "id": created.get("id"),
            "title": created.get("summary"),
            "start": created.get("start", {}).get("dateTime") or created.get("start", {}).get("date"),
            "end": created.get("end", {}).get("dateTime") or created.get("end", {}).get("date"),
            "attendees": attendee_emails,
            "htmlLink": created.get("htmlLink"),
        }
    except Exception as e:
        logger.error("Error in create_event: %s", _sanitize_error(e))
        return {
            "status": "error",
            "message": _sanitize_error(e),
        }


def list_upcoming_events(
    start_datetime: Optional[Union[str, datetime]] = None,
    end_datetime: Optional[Union[str, datetime]] = None,
    service: Any = None,
) -> List[Dict[str, Any]]:
    """
    Retrieve upcoming events from the primary Google Calendar.

    Args:
        start_datetime: Optional start window (ISO str or datetime). Defaults to now.
        end_datetime: Optional end window (ISO str or datetime).
        service: Optional pre-constructed Calendar API service instance.

    Returns:
        List of concise event dictionaries.
    """
    try:
        if service is None:
            service = get_calendar_service()

        time_min_str = None
        if isinstance(start_datetime, datetime):
            if start_datetime.tzinfo is None:
                start_datetime = start_datetime.astimezone()
            time_min_str = start_datetime.isoformat()
        elif isinstance(start_datetime, str) and start_datetime.strip():
            try:
                dt = datetime.fromisoformat(start_datetime.strip())
                if dt.tzinfo is None:
                    dt = dt.astimezone()
                time_min_str = dt.isoformat()
            except ValueError:
                time_min_str = start_datetime.strip()
        else:
            time_min_str = datetime.now().astimezone().isoformat()

        time_max_str = None
        if isinstance(end_datetime, datetime):
            if end_datetime.tzinfo is None:
                end_datetime = end_datetime.astimezone()
            time_max_str = end_datetime.isoformat()
        elif isinstance(end_datetime, str) and end_datetime.strip():
            try:
                dt = datetime.fromisoformat(end_datetime.strip())
                if dt.tzinfo is None:
                    dt = dt.astimezone()
                time_max_str = dt.isoformat()
            except ValueError:
                time_max_str = end_datetime.strip()

        params = {
            "calendarId": "primary",
            "singleEvents": True,
            "orderBy": "startTime",
            "timeMin": time_min_str,
        }
        if time_max_str:
            params["timeMax"] = time_max_str

        events_result = service.events().list(**params).execute()
        items = events_result.get("items", [])

        results = []
        for item in items:
            start_info = item.get("start", {})
            end_info = item.get("end", {})
            start_val = start_info.get("dateTime") or start_info.get("date")
            end_val = end_info.get("dateTime") or end_info.get("date")

            attendees = [
                a.get("email")
                for a in item.get("attendees", [])
                if isinstance(a, dict) and "email" in a
            ]

            results.append(
                {
                    "id": item.get("id"),
                    "title": item.get("summary", "(No Title)"),
                    "start": start_val,
                    "end": end_val,
                    "attendees": attendees,
                }
            )

        return results
    except Exception as e:
        logger.error("Error in list_upcoming_events: %s", _sanitize_error(e))
        return []
