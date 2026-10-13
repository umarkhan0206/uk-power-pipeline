"""Open-Meteo archived ECMWF forecasts at fixed points (Single Runs API).

For target day D we use the run that started at run_hour_utc on D-1. Open-Meteo
reports when a run started, not when it became available, so availability is an
explicit assumption (ASSUMED_RUN_AVAILABILITY) that is checked against the
decision time. See docs/data_sources.md.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ukpower.config import OpenMeteoSettings
from ukpower.ingestion.sources.base import (
    ResponseValidationError,
    Source,
    SourceRequest,
    latest_forecast_day,
)
from ukpower.timeutils import decision_time_utc, settlement_day_bounds_utc

#: Open-Meteo says global runs take about 4 to 6 hours to process. We assume the upper end.
ASSUMED_RUN_AVAILABILITY = timedelta(hours=6)
#: Largest allowed distance between a requested point and the grid point returned.
MAX_SNAP_DEGREES = 0.25
RUN_FORMAT = "%Y-%m-%dT%H:%M"


class OpenMeteoForecast(Source):
    """One request per target day covering every configured location. Partition = target date."""

    name = "open_meteo_ecmwf"

    #: An archived model run never changes, so there is nothing to refetch.
    refetch_recent = False

    def latest_available_day(self, now: datetime, tz: ZoneInfo) -> date:
        return latest_forecast_day(now, self._decision_local, self._tz)

    def __init__(self, settings: OpenMeteoSettings, tz: ZoneInfo, decision_local: time) -> None:
        self._settings = settings
        self._tz = tz
        self._decision_local = decision_local

    def run_for_day(self, day: date) -> datetime:
        """The forecast run used for a target day, checked against the decision time.

        Raises:
            ValueError: if the run would not be available by the decision time.
        """
        run = datetime.combine(
            day - timedelta(days=1), time(self._settings.run_hour_utc), tzinfo=UTC
        )
        decision = decision_time_utc(day, self._decision_local, self._tz)
        if run + ASSUMED_RUN_AVAILABILITY > decision:
            raise ValueError(f"run {run} is not assumed available by the decision time {decision}")
        return run

    def requests_for_day(self, day: date) -> list[SourceRequest]:
        locations = self._settings.locations
        return [
            SourceRequest(
                partition=day.isoformat(),
                url=self._settings.base_url,
                params={
                    "latitude": ",".join(str(loc.lat) for loc in locations),
                    "longitude": ",".join(str(loc.lon) for loc in locations),
                    "run": self.run_for_day(day).strftime(RUN_FORMAT),
                    "models": self._settings.model,
                    "hourly": ",".join(self._settings.hourly_variables),
                    "timezone": "GMT",
                },
            )
        ]

    def target_hours(self, day: date) -> set[str]:
        """Every hour of the local settlement day, as Open-Meteo GMT time strings."""
        start, end = settlement_day_bounds_utc(day, self._tz)
        hours = set()
        current = start
        while current < end:
            hours.add(current.strftime(RUN_FORMAT))
            current += timedelta(hours=1)
        return hours

    def validate(self, request: SourceRequest, payload: Any) -> int:  # noqa: ANN401
        locations = self._settings.locations
        variables = self._settings.hourly_variables
        items = [payload] if isinstance(payload, dict) else payload
        if not isinstance(items, list) or len(items) != len(locations):
            got = len(items) if isinstance(items, list) else type(items).__name__
            raise ResponseValidationError(
                f"{self.name}: expected {len(locations)} locations, got {got}"
            )

        needed = self.target_hours(date.fromisoformat(request.partition))
        total = 0
        for loc, item in zip(locations, items, strict=True):
            if not isinstance(item, dict) or not isinstance(item.get("hourly"), dict):
                raise ResponseValidationError(f"{self.name}: no hourly data for {loc.name}")

            lat, lon = item.get("latitude"), item.get("longitude")
            if (
                not isinstance(lat, int | float)
                or not isinstance(lon, int | float)
                or abs(lat - loc.lat) > MAX_SNAP_DEGREES
                or abs(lon - loc.lon) > MAX_SNAP_DEGREES
            ):
                raise ResponseValidationError(
                    f"{self.name}: {loc.name} requested ({loc.lat}, {loc.lon}), got ({lat}, {lon})"
                )

            hourly = item["hourly"]
            times = hourly.get("time")
            missing = [v for v in variables if v not in hourly]
            if not isinstance(times, list) or missing:
                raise ResponseValidationError(
                    f"{self.name}: {loc.name} missing time or variables {missing}"
                )
            if any(len(hourly[v]) != len(times) for v in variables):
                raise ResponseValidationError(f"{self.name}: {loc.name} has unequal series lengths")
            if not needed <= set(times):
                raise ResponseValidationError(
                    f"{self.name}: {loc.name} does not cover {request.partition}, "
                    f"{len(needed - set(times))} hours missing"
                )
            total += len(times)

        return total
