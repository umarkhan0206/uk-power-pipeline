"""National Grid ESO carbon intensity, half hourly.

The API returns any window touching the start time, so one extra window before
the day is expected. It gives no publish time, so its forecast is never used as
a model input; only actual values are used. See docs/data_sources.md.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ukpower.ingestion.sources.base import (
    ResponseValidationError,
    Source,
    SourceRequest,
    require_data_list,
)
from ukpower.timeutils import SETTLEMENT_PERIOD, settlement_day_bounds_utc

PATH_TIME_FORMAT = "%Y-%m-%dT%H:%MZ"


def parse_window_time(value: str) -> datetime:
    """Parse a carbon intensity time such as 2026-10-04T23:30Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class CarbonIntensity(Source):
    """One request per settlement date. Partition = the settlement date."""

    name = "carbon_intensity"

    def __init__(self, base_url: str, tz: ZoneInfo) -> None:
        self._base_url = base_url
        self._tz = tz

    def requests_for_day(self, day: date) -> list[SourceRequest]:
        start, end = settlement_day_bounds_utc(day, self._tz)
        return [
            SourceRequest(
                partition=day.isoformat(),
                url=(
                    f"{self._base_url}/intensity/"
                    f"{start.strftime(PATH_TIME_FORMAT)}/{end.strftime(PATH_TIME_FORMAT)}"
                ),
            )
        ]

    def validate(self, request: SourceRequest, payload: Any) -> int:  # noqa: ANN401
        records = require_data_list(payload)
        day = date.fromisoformat(request.partition)
        start, end = settlement_day_bounds_utc(day, self._tz)
        earliest_allowed = start - SETTLEMENT_PERIOD  # the API adds the window touching the start

        starts = []
        for r in records:
            try:
                window_start = parse_window_time(r["from"])
                window_end = parse_window_time(r["to"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ResponseValidationError(f"{self.name}: bad window {r!r}") from exc
            if window_end - window_start != SETTLEMENT_PERIOD:
                raise ResponseValidationError(
                    f"{self.name}: window {r['from']} to {r['to']} is not 30 minutes"
                )
            if not earliest_allowed <= window_start < end:
                raise ResponseValidationError(
                    f"{self.name}: window {r['from']} outside {request.partition}"
                )
            if not isinstance(r.get("intensity"), dict):
                raise ResponseValidationError(f"{self.name}: window {r['from']} has no intensity")
            starts.append(window_start)

        duplicates = sorted(str(s) for s, n in Counter(starts).items() if n > 1)
        if duplicates:
            raise ResponseValidationError(f"{self.name}: duplicate windows {duplicates[:5]}")

        expected = set()
        current = start
        while current < end:
            expected.add(current)
            current += SETTLEMENT_PERIOD
        missing = expected - set(starts)
        if missing:
            raise ResponseValidationError(
                f"{self.name}: {len(missing)} windows of {request.partition} missing"
            )

        return len(records)
