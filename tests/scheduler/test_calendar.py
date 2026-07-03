import json
from datetime import date, time

import pytest

from aeolus.scheduler.calendar import DEFAULT_CLOSE, DEFAULT_OPEN, NseCalendar


@pytest.fixture
def calendar_path(tmp_path):
    path = tmp_path / "nse_holidays.json"
    path.write_text(
        json.dumps(
            {
                "holidays": ["2026-01-26", "2026-11-08"],
                "special_sessions": {"2026-11-08": {"open": "18:00", "close": "19:00"}},
            }
        )
    )
    return path


def test_weekend_is_not_a_trading_day(calendar_path):
    calendar = NseCalendar(calendar_path)
    saturday = date(2026, 7, 4)
    assert saturday.weekday() == 5
    assert not calendar.is_trading_day(saturday)
    assert calendar.session_hours(saturday) is None


def test_configured_holiday_is_not_a_trading_day(calendar_path):
    calendar = NseCalendar(calendar_path)
    republic_day = date(2026, 1, 26)
    assert not calendar.is_trading_day(republic_day)
    assert calendar.session_hours(republic_day) is None


def test_ordinary_weekday_gets_default_hours(calendar_path):
    calendar = NseCalendar(calendar_path)
    ordinary_day = date(2026, 7, 6)  # a Monday, not in the fixture's holidays
    assert calendar.is_trading_day(ordinary_day)
    hours = calendar.session_hours(ordinary_day)
    assert hours is not None
    assert hours.open == DEFAULT_OPEN
    assert hours.close == DEFAULT_CLOSE


def test_special_session_overrides_hours_and_still_counts_as_trading_day(calendar_path):
    calendar = NseCalendar(calendar_path)
    diwali = date(2026, 11, 8)  # both a holiday AND a muhurat special session
    assert calendar.is_trading_day(diwali)
    hours = calendar.session_hours(diwali)
    assert hours is not None
    assert hours.open == time(18, 0)
    assert hours.close == time(19, 0)
