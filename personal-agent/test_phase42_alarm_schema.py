import pytest
from pydantic import ValidationError
from typing import get_args

from agent.schemas import (
    SetAlarmArgs,
    ToolName,
    ToolCall,
    TOOL_ARGS_SCHEMAS,
)
from agent.core import validate_tool_call


def test_01_valid_set_alarm_args():
    """1. Valid SetAlarmArgs with required fields."""
    args = SetAlarmArgs(
        message="Team sync meeting",
        fire_at="2026-09-11T09:00:00"
    )
    assert args.message == "Team sync meeting"
    assert args.fire_at == "2026-09-11T09:00:00"
    assert args.offset_minutes is None
    assert args.reference_time is None


def test_02_empty_message_rejected():
    """2. Empty or whitespace-only message rejected by Pydantic validator."""
    with pytest.raises(ValidationError) as exc_info:
        SetAlarmArgs(message="", fire_at="2026-09-11T09:00:00")
    assert "Message must not be empty" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info2:
        SetAlarmArgs(message="   ", fire_at="2026-09-11T09:00:00")
    assert "Message must not be empty" in str(exc_info2.value)


def test_03_invalid_fire_at_rejected():
    """3. Invalid fire_at datetime string format rejected."""
    with pytest.raises(ValidationError) as exc_info:
        SetAlarmArgs(message="Water plants", fire_at="invalid-date-string")
    assert "Invalid ISO datetime string" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info2:
        SetAlarmArgs(message="Water plants", fire_at="")
    assert "fire_at must not be empty" in str(exc_info2.value)


def test_04_valid_offset_minutes():
    """4. Valid positive and negative integer offset_minutes accepted."""
    args_pos = SetAlarmArgs(
        message="30 mins before meeting",
        fire_at="2026-09-11T08:30:00",
        offset_minutes=30
    )
    assert args_pos.offset_minutes == 30

    args_neg = SetAlarmArgs(
        message="15 mins prior",
        fire_at="2026-09-11T08:45:00",
        offset_minutes=-15
    )
    assert args_neg.offset_minutes == -15


def test_05_invalid_offset_minutes():
    """5. Non-integer offset_minutes rejected."""
    with pytest.raises(ValidationError):
        SetAlarmArgs(
            message="Check oven",
            fire_at="2026-09-11T09:00:00",
            offset_minutes="invalid_number"
        )


def test_06_valid_reference_time():
    """6. Valid reference_time ISO format accepted."""
    args = SetAlarmArgs(
        message="Meeting reminder",
        fire_at="2026-09-11T08:30:00",
        offset_minutes=30,
        reference_time="2026-09-11T09:00:00"
    )
    assert args.reference_time == "2026-09-11T09:00:00"

    # YYYY-MM-DD date format also valid ISO date
    args2 = SetAlarmArgs(
        message="Daily digest",
        fire_at="2026-09-11T09:00:00",
        reference_time="2026-09-11"
    )
    assert args2.reference_time == "2026-09-11"


def test_07_invalid_reference_time():
    """7. Invalid reference_time ISO format rejected."""
    with pytest.raises(ValidationError) as exc_info:
        SetAlarmArgs(
            message="Meeting reminder",
            fire_at="2026-09-11T08:30:00",
            reference_time="not-a-datetime"
        )
    assert "Invalid ISO datetime string" in str(exc_info.value)


def test_08_set_alarm_exists_in_toolname():
    """8. set_alarm is present in ToolName Literal enum."""
    literals = get_args(ToolName)
    assert "set_alarm" in literals


def test_09_set_alarm_maps_to_setalarmargs():
    """9. set_alarm maps to SetAlarmArgs in TOOL_ARGS_SCHEMAS."""
    assert "set_alarm" in TOOL_ARGS_SCHEMAS
    assert TOOL_ARGS_SCHEMAS["set_alarm"] == SetAlarmArgs


def test_10_valid_set_alarm_toolcall_passes_validation():
    """10. Valid set_alarm ToolCall passes validate_tool_call() without modification."""
    tc = ToolCall(
        tool="set_alarm",
        args={
            "message": "Doctor appointment",
            "fire_at": "2026-09-12T10:00:00"
        },
        reasoning="Setting alarm for doctor appointment."
    )
    validated = validate_tool_call(tc, user_message="Remind me for doctor appointment")
    assert validated.tool == "set_alarm"
    assert validated.args["message"] == "Doctor appointment"
    assert validated.args["fire_at"] == "2026-09-12T10:00:00"


def test_11_malformed_set_alarm_toolcall_overridden():
    """11. Malformed set_alarm ToolCall is safely overridden to tool='none'."""
    tc = ToolCall(
        tool="set_alarm",
        args={
            "message": "",
            "fire_at": "bad-datetime-format"
        },
        reasoning="Malformed alarm tool call."
    )
    validated = validate_tool_call(tc, user_message="Remind me something")
    assert validated.tool == "none"
    assert "Invalid arguments provided for tool set_alarm" in validated.args["message"]
