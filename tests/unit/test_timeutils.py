"""Tests for UK settlement day arithmetic, including clock change days."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from ukpower.timeutils import (
    decision_time_utc,
    settlement_day_bounds_utc,
    settlement_periods_in_day,
)

UK = ZoneInfo("Europe/London")


def test_summer_day_bounds_match_observed_elexon_data() -> None:
    # Observed in the API probe: period 1 of 2026-10-05 started at 2026-10-04T23:00:00Z.
    start, end = settlement_day_bounds_utc(date(2026, 10, 5), UK)
    assert start == datetime(2026, 10, 4, 23, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 5, 23, 0, tzinfo=UTC)


def test_winter_day_bounds_are_midnight_to_midnight_utc() -> None:
    start, end = settlement_day_bounds_utc(date(2026, 12, 15), UK)
    assert start == datetime(2026, 12, 15, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 12, 16, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 5), 48),  # ordinary day
        (date(2025, 3, 30), 46),  # clocks forward
        (date(2025, 10, 26), 50),  # clocks back
        (date(2026, 3, 29), 46),  # clocks forward
        (date(2026, 10, 25), 50),  # clocks back
    ],
)
def test_settlement_periods_in_day(day: date, expected: int) -> None:
    assert settlement_periods_in_day(day, UK) == expected


def test_decision_time_in_summer_is_0900_utc() -> None:
    result = decision_time_utc(date(2026, 10, 6), time(10, 0), UK)
    assert result == datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def test_decision_time_in_winter_is_1000_utc() -> None:
    result = decision_time_utc(date(2026, 12, 15), time(10, 0), UK)
    assert result == datetime(2026, 12, 14, 10, 0, tzinfo=UTC)


def test_decision_time_on_clock_change_day_uses_new_offset() -> None:
    # Clocks went back at 02:00 on 2026-10-25, so 10:00 that day is GMT.
    result = decision_time_utc(date(2026, 10, 26), time(10, 0), UK)
    assert result == datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
