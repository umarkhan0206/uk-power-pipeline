"""Elexon Market Index Data (MID): traded price and volume per half hour, per exchange.

Only APX (APXMIDP) carries real trades; N2EX (N2EXMIDP) rows are almost always price 0
with volume 0. Bronze keeps every row as received; Silver treats volume 0 as missing.
The dataset has no publish time. Observed on 27 Oct 2026: a half hour's value was out
within about 22 minutes of its start, so the model only uses values from 2 days back.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ukpower.ingestion.sources.base import (
    ResponseValidationError,
    Source,
    SourceRequest,
    require_data_list,
)
from ukpower.timeutils import settlement_periods_in_day

KNOWN_PROVIDERS = frozenset({"APXMIDP", "N2EXMIDP"})
TIME_FORMAT = "%Y-%m-%dT%H:%MZ"


def _is_number(value: Any) -> bool:  # noqa: ANN401
    return isinstance(value, int | float) and not isinstance(value, bool)


class ElexonMarketIndex(Source):
    """One request per UK settlement day. Partition = the settlement date.

    The endpoint selects rows by period start time in UTC, both ends included, so each
    request runs from the UK day's first period start to its last.
    """

    name = "elexon_market_index"

    def __init__(self, base_url: str, tz: ZoneInfo) -> None:
        self._base_url = base_url
        self._tz = tz

    def day_window_utc(self, day: date) -> tuple[datetime, datetime]:
        """Start times of the first and last half hour of a UK settlement day, in UTC."""
        first = datetime.combine(day, time(0), tzinfo=self._tz).astimezone(UTC)
        next_day = datetime.combine(day + timedelta(days=1), time(0), tzinfo=self._tz)
        return first, next_day.astimezone(UTC) - timedelta(minutes=30)

    def requests_for_day(self, day: date) -> list[SourceRequest]:
        first, last = self.day_window_utc(day)
        return [
            SourceRequest(
                partition=day.isoformat(),
                url=f"{self._base_url}/datasets/MID",
                params={
                    "from": first.strftime(TIME_FORMAT),
                    "to": last.strftime(TIME_FORMAT),
                    "format": "json",
                },
            )
        ]

    def validate(self, request: SourceRequest, payload: Any) -> int:  # noqa: ANN401
        records = require_data_list(payload)
        if not records:
            raise ResponseValidationError(f"{self.name}: no records for {request.partition}")
        day = date.fromisoformat(request.partition)
        max_periods = settlement_periods_in_day(day, self._tz)

        wrong_dates = {r.get("settlementDate") for r in records} - {request.partition}
        if wrong_dates:
            raise ResponseValidationError(
                f"records for unexpected dates {sorted(map(str, wrong_dates))} "
                f"in response for {request.partition}"
            )

        periods = [r.get("settlementPeriod") for r in records]
        bad = [p for p in periods if not isinstance(p, int) or not 1 <= p <= max_periods]
        if bad:
            raise ResponseValidationError(
                f"invalid settlement periods {sorted(map(str, set(bad)))} for {request.partition} "
                f"(day has {max_periods} periods)"
            )

        unknown = {r.get("dataProvider") for r in records} - KNOWN_PROVIDERS
        if unknown:
            raise ResponseValidationError(
                f"unknown data providers {sorted(map(str, unknown))} for {request.partition}"
            )

        keys = Counter((r["settlementPeriod"], r["dataProvider"]) for r in records)
        duplicates = sorted(k for k, n in keys.items() if n > 1)
        if duplicates:
            raise ResponseValidationError(
                f"duplicate (period, provider) {duplicates} for {request.partition}"
            )

        bad_numbers = sorted(
            {
                r["settlementPeriod"]
                for r in records
                if not (_is_number(r.get("price")) and _is_number(r.get("volume")))
            }
        )
        if bad_numbers:
            raise ResponseValidationError(
                f"missing or non numeric price or volume in periods {bad_numbers} "
                f"for {request.partition}"
            )
        return len(records)
