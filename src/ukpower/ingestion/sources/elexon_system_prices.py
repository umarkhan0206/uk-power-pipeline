"""Elexon system prices: the half hourly imbalance price we forecast."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from ukpower.ingestion.sources.base import (
    ResponseValidationError,
    Source,
    SourceRequest,
    require_data_list,
)
from ukpower.timeutils import settlement_periods_in_day


class ElexonSystemPrices(Source):
    """One request per settlement date. Partition = the settlement date."""

    name = "elexon_system_prices"

    def __init__(self, base_url: str, tz: ZoneInfo) -> None:
        self._base_url = base_url
        self._tz = tz

    def requests_for_day(self, day: date) -> list[SourceRequest]:
        return [
            SourceRequest(
                partition=day.isoformat(),
                url=f"{self._base_url}/balancing/settlement/system-prices/{day.isoformat()}",
                params={"format": "json"},
            )
        ]

    def validate(self, request: SourceRequest, payload: Any) -> int:  # noqa: ANN401
        records = require_data_list(payload)
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
                f"invalid settlement periods {bad} for {request.partition} "
                f"(day has {max_periods} periods)"
            )

        duplicates = sorted(p for p, n in Counter(periods).items() if n > 1)
        if duplicates:
            raise ResponseValidationError(
                f"duplicate settlement periods {duplicates} for {request.partition}"
            )

        return len(records)
