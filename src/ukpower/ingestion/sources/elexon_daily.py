"""Elexon datasets requested one settlement date at a time.

The shared class holds the validation; each dataset only declares its path,
its parameters and which fields make a record unique within a period.
"""

from __future__ import annotations

from abc import abstractmethod
from collections import Counter
from datetime import date
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from ukpower.ingestion.sources.base import (
    ResponseValidationError,
    Source,
    SourceRequest,
    require_data_list,
)
from ukpower.timeutils import settlement_periods_in_day


class ElexonDailySource(Source):
    """One request per settlement date. Partition = the settlement date."""

    #: Endpoint path after the base URL.
    path: ClassVar[str]
    #: Fields that, together with settlementPeriod, identify one record.
    key_fields: ClassVar[tuple[str, ...]] = ()

    def __init__(self, base_url: str, tz: ZoneInfo) -> None:
        self._base_url = base_url
        self._tz = tz

    @abstractmethod
    def params_for_day(self, day: date) -> dict[str, str | int | float]:
        """Query parameters that select exactly one settlement date."""

    def requests_for_day(self, day: date) -> list[SourceRequest]:
        return [
            SourceRequest(
                partition=day.isoformat(),
                url=f"{self._base_url}{self.path}",
                params={**self.params_for_day(day), "format": "json"},
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
                f"{self.name}: records for unexpected dates {sorted(map(str, wrong_dates))} "
                f"in response for {request.partition}"
            )

        periods = [r.get("settlementPeriod") for r in records]
        bad = sorted(
            {p for p in periods if not isinstance(p, int) or not 1 <= p <= max_periods}, key=str
        )
        if bad:
            raise ResponseValidationError(
                f"{self.name}: invalid settlement periods {bad} for {request.partition} "
                f"(day has {max_periods} periods)"
            )

        keys = [(r.get("settlementPeriod"), *(r.get(f) for f in self.key_fields)) for r in records]
        duplicates = sorted((k for k, n in Counter(keys).items() if n > 1), key=str)
        if duplicates:
            raise ResponseValidationError(
                f"{self.name}: duplicate records {duplicates[:5]} for {request.partition}"
            )

        return len(records)


class ElexonDemandOutturn(ElexonDailySource):
    """National and transmission demand outturn (INDO, ITSDO). One row per period."""

    name = "elexon_demand_outturn"
    path = "/demand/outturn"

    def params_for_day(self, day: date) -> dict[str, str | int | float]:
        return {"settlementDateFrom": day.isoformat(), "settlementDateTo": day.isoformat()}


class ElexonGenerationByFuel(ElexonDailySource):
    """Generation by fuel type (FUELHH). One row per period per fuel type."""

    name = "elexon_generation_by_fuel"
    path = "/datasets/FUELHH"
    key_fields = ("fuelType",)

    def params_for_day(self, day: date) -> dict[str, str | int | float]:
        return {"settlementDateFrom": day.isoformat(), "settlementDateTo": day.isoformat()}


class ElexonWindSolarActual(ElexonDailySource):
    """Actual or estimated wind and solar (AGWS / B1630). One row per period per type."""

    name = "elexon_wind_solar_actual"
    path = "/generation/actual/per-type/wind-and-solar"
    # A period can be republished later with corrected values, so the same
    # (period, type) appears once per publishTime. Only a repeat with the same
    # publishTime is a true duplicate. Silver keeps the latest version.
    key_fields = ("psrType", "publishTime")

    def params_for_day(self, day: date) -> dict[str, str | int | float]:
        # Period parameters make from/to act as settlement dates (seen in the API probe).
        # 50 covers the longest possible day.
        return {
            "from": day.isoformat(),
            "to": day.isoformat(),
            "settlementPeriodFrom": 1,
            "settlementPeriodTo": 50,
        }


class ElexonWindSolarDayAhead(ElexonDailySource):
    """Day ahead wind and solar forecast (DGWS / B1440). One row per period per type.

    Published about 16:45 UTC on the day before, after the 10:00 decision time,
    so it is collected for comparison and an evening decision experiment,
    never used as an input to the 10:00 forecast.
    """

    name = "elexon_wind_solar_day_ahead"
    path = "/forecast/generation/wind-and-solar/day-ahead"
    key_fields = ("psrType", "publishTime")

    def params_for_day(self, day: date) -> dict[str, str | int | float]:
        return {
            "from": day.isoformat(),
            "to": day.isoformat(),
            "settlementPeriodFrom": 1,
            "settlementPeriodTo": 50,
            "processType": "day ahead",
        }
