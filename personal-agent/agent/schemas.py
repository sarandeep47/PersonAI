# agent/schemas.py
from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from typing import List, Literal, Union, Dict, Any, Optional

class SendEmailArgs(BaseModel):
    to: str = Field(..., description="Recipient email address — must be a valid email")
    subject: str = Field(..., description="Short, natural email subject line")
    body: str = Field(..., description="Full polished email body in first person")

class SearchInboxArgs(BaseModel):
    query: str = Field(..., description="Gmail search query (e.g. 'from:boss invoice')")
    max_results: int = Field(default=5, le=20)

class ReadEmailArgs(BaseModel):
    email_id: str = Field(..., description="ID of the email to read")

class DraftReplyArgs(BaseModel):
    email_id: str = Field(..., description="ID of the email to reply to")
    instructions: str = Field(..., description="What to say in the reply")

class ExportContactsArgs(BaseModel):
    pass

class DeleteContactArgs(BaseModel):
    query: str = Field(..., description="Name, nickname, or role of the contact to delete (e.g. 'hr', 'John')")

class RenameContactArgs(BaseModel):
    query: str = Field(..., description="Current name, nickname, or role identifying the contact to rename (e.g. 'hr', 'Shylaja')")
    new_name: str = Field(..., description="The new display name to assign to the contact")

class NoneArgs(BaseModel):
    message: str = Field(..., description="Response/question to show to the user")

class ScheduleCalendarArgs(BaseModel):
    title: str = Field(..., description="Title or summary of the calendar event")
    date: str = Field(..., description="Date string in YYYY-MM-DD format")
    start_time: str = Field(..., description="Start time string (e.g. '14:00' or '02:00 PM')")
    duration_minutes: int = Field(..., gt=0, description="Duration in minutes (must be > 0)")
    attendees: Optional[List[str]] = Field(default=None, description="Optional list of attendee email addresses")

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Title must not be empty.")
        return v.strip()

    @field_validator("date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        clean = v.strip()
        try:
            datetime.strptime(clean, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Date must be in YYYY-MM-DD format.")
        return clean

    @field_validator("start_time")
    @classmethod
    def validate_start_time(cls, v: str) -> str:
        clean = v.strip()
        valid = False
        for fmt in ["%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"]:
            try:
                datetime.strptime(clean, fmt)
                valid = True
                break
            except ValueError:
                continue
        if not valid:
            raise ValueError("Start time must be a valid time string (e.g., '14:00' or '02:00 PM').")
        return clean

    @field_validator("attendees")
    @classmethod
    def validate_attendees(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        for email in v:
            if not isinstance(email, str) or "@" not in email or "." not in email:
                raise ValueError(f"Invalid attendee email: {email}")
        return v

class ListCalendarArgs(BaseModel):
    start_datetime: Optional[str] = Field(default=None, description="Optional ISO format start datetime string")
    end_datetime: Optional[str] = Field(default=None, description="Optional ISO format end datetime string")

    @field_validator("start_datetime", "end_datetime")
    @classmethod
    def validate_iso_dt(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        clean = v.strip()
        try:
            if len(clean) == 10:
                datetime.strptime(clean, "%Y-%m-%d")
            else:
                datetime.fromisoformat(clean)
        except ValueError:
            raise ValueError(f"Invalid ISO datetime string: '{clean}'")
        return clean

class SetAlarmArgs(BaseModel):
    message: str = Field(..., description="Alarm or reminder message text")
    fire_at: str = Field(..., description="ISO format datetime string when the alarm should fire")
    offset_minutes: Optional[int] = Field(default=None, description="Optional offset in minutes relative to reference_time or event")
    reference_time: Optional[str] = Field(default=None, description="Optional ISO format reference datetime string")

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Message must not be empty.")
        return v.strip()

    @field_validator("fire_at")
    @classmethod
    def validate_fire_at(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("fire_at must not be empty.")
        clean = v.strip()
        try:
            if len(clean) == 10:
                datetime.strptime(clean, "%Y-%m-%d")
            else:
                datetime.fromisoformat(clean)
        except ValueError:
            raise ValueError(f"Invalid ISO datetime string: '{clean}'")
        return clean

    @field_validator("reference_time")
    @classmethod
    def validate_reference_time(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        clean = v.strip()
        try:
            if len(clean) == 10:
                datetime.strptime(clean, "%Y-%m-%d")
            else:
                datetime.fromisoformat(clean)
        except ValueError:
            raise ValueError(f"Invalid ISO datetime string: '{clean}'")
        return clean

ToolName = Literal[
    "send_email",
    "search_inbox",
    "read_email",
    "draft_reply",
    "export_contacts",
    "delete_contact",
    "rename_contact",
    "schedule_calendar",
    "list_calendar",
    "set_alarm",
    "set_reminder",
    "none",
]

class ToolCall(BaseModel):
    tool: ToolName
    args: Dict[str, Any] = Field(default_factory=dict)
    reasoning: str = Field(default="No reasoning provided.", description="One sentence explaining why this tool was chosen")

    @field_validator("tool", mode="before")
    @classmethod
    def normalize_tool_name(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_clean = v.strip().lower()
            if v_clean in ("set_reminder", "reminder", "alarm", "set_alarm"):
                return "set_alarm"
        return v

class TaskPlan(BaseModel):
    tasks: List[ToolCall]
    reasoning: str

TOOL_ARGS_SCHEMAS: Dict[str, type[BaseModel]] = {
    "send_email": SendEmailArgs,
    "search_inbox": SearchInboxArgs,
    "read_email": ReadEmailArgs,
    "draft_reply": DraftReplyArgs,
    "export_contacts": ExportContactsArgs,
    "delete_contact": DeleteContactArgs,
    "rename_contact": RenameContactArgs,
    "schedule_calendar": ScheduleCalendarArgs,
    "list_calendar": ListCalendarArgs,
    "set_alarm": SetAlarmArgs,
    "set_reminder": SetAlarmArgs,
    "none": NoneArgs,
}


