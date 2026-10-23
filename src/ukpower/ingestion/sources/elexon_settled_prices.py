"""Elexon settled system prices: the latest official settlement run for each half hour.

The same half hourly price as elexon_system_prices, taken from the best view report.
Each day is recalculated in settlement runs over about 14 months:
II, then SF, R1, R2, R3 and RF (DF only after a dispute). Before the first run
exists, the report falls back to the market feed and labels it BM.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from ukpower.ingestion.sources.base import ResponseValidationError, SourceRequest, require_data_list
from ukpower.ingestion.sources.elexon_system_prices import ElexonSystemPrices

KNOWN_RUN_TYPES = frozenset({"BM", "II", "SF", "R1", "R2", "R3", "RF", "DF"})
PRICE_FIELDS = ("systemSellPrice", "systemBuyPrice")


def _is_number(value: Any) -> bool:  # noqa: ANN401
    return isinstance(value, int | float) and not isinstance(value, bool)


class ElexonSettledPrices(ElexonSystemPrices):
    """One request per settlement date, no run type, so Elexon returns its best (latest) run.

    Later runs arrive over about 14 months, so once a week this source looks much
    further back than the others. Elexon keeps each run available for months,
    so a weekly look back misses no run, only its exact arrival day.
    """

    name = "elexon_settled_prices"

    def __init__(
        self, base_url: str, tz: ZoneInfo, long_refetch_days: int, long_refetch_weekday: int
    ) -> None:
        super().__init__(base_url, tz)
        self._long_refetch_days = long_refetch_days
        self._long_refetch_weekday = long_refetch_weekday

    def requests_for_day(self, day: date) -> list[SourceRequest]:
        return [
            SourceRequest(
                partition=day.isoformat(),
                url=f"{self._base_url}/market-reports/best-view-prices",
                params={"startDate": day.isoformat(), "endDate": day.isoformat(), "format": "json"},
            )
        ]

    def refetch_days(self, default: int, today: date) -> int:
        if today.weekday() == self._long_refetch_weekday:
            return max(default, self._long_refetch_days)
        return default

    def validate(self, request: SourceRequest, payload: Any) -> int:  # noqa: ANN401
        count = super().validate(request, payload)
        records = require_data_list(payload)

        unknown = {r.get("runType") for r in records} - KNOWN_RUN_TYPES
        if unknown:
            raise ResponseValidationError(
                f"unknown run types {sorted(map(str, unknown))} for {request.partition}"
            )

        bad_prices = sorted(
            r["settlementPeriod"]
            for r in records
            if not all(_is_number(r.get(f)) for f in PRICE_FIELDS)
        )
        if bad_prices:
            raise ResponseValidationError(
                f"missing or non numeric prices in periods {bad_prices} for {request.partition}"
            )
        return count
