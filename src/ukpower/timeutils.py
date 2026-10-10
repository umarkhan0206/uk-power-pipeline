"""UK settlement day arithmetic.

GB settlement days follow UK local time, so they are 23, 24 or 25 hours long.
Everything here returns timezone aware UTC datetimes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SETTLEMENT_PERIOD = timedelta(minutes=30)


def settlement_day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC start (inclusive) and end (exclusive) of a local settlement day."""
    start = datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)
    return start, end


def settlement_periods_in_day(day: date, tz: ZoneInfo) -> int:
    """Number of half hour periods: 48 normally, 46 when clocks go forward, 50 when they go back."""
    start, end = settlement_day_bounds_utc(day, tz)
    return int((end - start) / SETTLEMENT_PERIOD)


def decision_time_utc(target_day: date, decision_local: time, tz: ZoneInfo) -> datetime:
    """UTC moment when forecasts for target_day are made: decision_local on the day before."""
    decision_day = target_day - timedelta(days=1)
    return datetime.combine(decision_day, decision_local, tzinfo=tz).astimezone(UTC)
