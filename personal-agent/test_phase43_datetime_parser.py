import pytest
from datetime import datetime, timezone, timedelta
from agent.core import parse_natural_datetime

# Explicit reference datetime: Thursday, September 10, 2026 at 20:00:00
REF_DT = datetime(2026, 9, 10, 20, 0, 0)


def test_01_tomorrow_at_9_am():
    """1. Test 'tomorrow at 9 AM'."""
    res = parse_natural_datetime("tomorrow at 9 AM", reference_datetime=REF_DT)
    assert res == "2026-09-11T09:00:00"


def test_02_tomorrow_at_9_30_am():
    """2. Test 'tomorrow at 9:30 AM'."""
    res = parse_natural_datetime("tomorrow at 9:30 AM", reference_datetime=REF_DT)
    assert res == "2026-09-11T09:30:00"


def test_03_today_at_6_pm():
    """3. Test 'today at 6 PM'."""
    res = parse_natural_datetime("today at 6 PM", reference_datetime=REF_DT)
    assert res == "2026-09-10T18:00:00"


def test_04_friday_at_2_30_pm():
    """4. Test 'Friday at 2:30 PM' relative to Thursday Sept 10."""
    res = parse_natural_datetime("Friday at 2:30 PM", reference_datetime=REF_DT)
    assert res == "2026-09-11T14:30:00"


def test_05_this_friday_at_2_30_pm():
    """5. Test 'this Friday at 2:30 PM' relative to Thursday Sept 10."""
    res = parse_natural_datetime("this Friday at 2:30 PM", reference_datetime=REF_DT)
    assert res == "2026-09-11T14:30:00"


def test_06_next_friday_at_2_30_pm():
    """6. Test 'next Friday at 2:30 PM' relative to Thursday Sept 10."""
    res = parse_natural_datetime("next Friday at 2:30 PM", reference_datetime=REF_DT)
    assert res == "2026-09-18T14:30:00"


def test_07_weekday_monday():
    """7. Test another weekday such as 'next Monday at 10 AM'."""
    res = parse_natural_datetime("next Monday at 10 AM", reference_datetime=REF_DT)
    assert res == "2026-09-21T10:00:00"

    res_this_mon = parse_natural_datetime("Monday at 10 AM", reference_datetime=REF_DT)
    assert res_this_mon == "2026-09-14T10:00:00"


def test_08_24_hour_time():
    """8. Test 24-hour time format e.g. 'tomorrow at 14:30'."""
    res = parse_natural_datetime("tomorrow at 14:30", reference_datetime=REF_DT)
    assert res == "2026-09-11T14:30:00"

    res2 = parse_natural_datetime("today at 18:00", reference_datetime=REF_DT)
    assert res2 == "2026-09-10T18:00:00"


def test_09_lowercase_am_pm():
    """9. Test lowercase 'am' / 'pm'."""
    res1 = parse_natural_datetime("tomorrow at 9:30 am", reference_datetime=REF_DT)
    assert res1 == "2026-09-11T09:30:00"

    res2 = parse_natural_datetime("friday at 2:30 pm", reference_datetime=REF_DT)
    assert res2 == "2026-09-11T14:30:00"


def test_10_in_30_minutes():
    """10. Test relative duration 'in 30 minutes'."""
    res = parse_natural_datetime("in 30 minutes", reference_datetime=REF_DT)
    assert res == "2026-09-10T20:30:00"


def test_11_in_2_hours():
    """11. Test relative duration 'in 2 hours'."""
    res = parse_natural_datetime("in 2 hours", reference_datetime=REF_DT)
    assert res == "2026-09-10T22:00:00"


def test_12_in_90_minutes():
    """12. Test relative duration 'in 90 minutes'."""
    res = parse_natural_datetime("in 90 minutes", reference_datetime=REF_DT)
    assert res == "2026-09-10T21:30:00"


def test_13_invalid_time_rejected():
    """13. Test invalid times returning None."""
    assert parse_natural_datetime("tomorrow at 25:00", reference_datetime=REF_DT) is None
    assert parse_natural_datetime("today at 13:90", reference_datetime=REF_DT) is None
    assert parse_natural_datetime("tomorrow at 15 PM", reference_datetime=REF_DT) is None


def test_14_invalid_weekday_date_expression():
    """14. Test invalid weekday/date expression returning None."""
    assert parse_natural_datetime("someday at 9 AM", reference_datetime=REF_DT) is None
    assert parse_natural_datetime("blabla at 9 AM", reference_datetime=REF_DT) is None


def test_15_unparseable_input_returns_none():
    """15. Test unparseable natural-language input returns None."""
    assert parse_natural_datetime("hello world", reference_datetime=REF_DT) is None
    assert parse_natural_datetime("remind me to buy milk", reference_datetime=REF_DT) is None
    assert parse_natural_datetime("", reference_datetime=REF_DT) is None
    assert parse_natural_datetime(None, reference_datetime=REF_DT) is None


def test_16_exact_iso_style_datetime():
    """16. Test exact ISO-style datetime strings."""
    assert parse_natural_datetime("2026-09-11T09:00:00", reference_datetime=REF_DT) == "2026-09-11T09:00:00"
    assert parse_natural_datetime("2026-09-11", reference_datetime=REF_DT) == "2026-09-11T00:00:00"


def test_17_timezone_aware_reference_datetime():
    """17. Test timezone-aware reference datetime behavior."""
    tz_ny = timezone(timedelta(hours=-5))
    ref_tz = datetime(2026, 9, 10, 20, 0, 0, tzinfo=tz_ny)

    res = parse_natural_datetime("in 30 minutes", reference_datetime=ref_tz)
    assert res == "2026-09-10T20:30:00"


def test_18_reference_datetime_injection_deterministic():
    """18. Test that changing injected reference datetime deterministically shifts outputs."""
    ref1 = datetime(2026, 9, 10, 10, 0, 0)
    ref2 = datetime(2026, 12, 24, 15, 0, 0)

    res1 = parse_natural_datetime("tomorrow at 9 AM", reference_datetime=ref1)
    res2 = parse_natural_datetime("tomorrow at 9 AM", reference_datetime=ref2)

    assert res1 == "2026-09-11T09:00:00"
    assert res2 == "2026-12-25T09:00:00"
