"""Elexon forecasts, as known at the decision time.

Forecasts are reissued many times, and a single issue may not reach the whole
target day. For each target day we request the forecast as of the decision
time, then step back through earlier issues until every time slot of the day
is covered. See docs/data_sources.md.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, ClassVar
from zoneinfo import ZoneInfo

from ukpower.ingestion.sources.base import (
    FetchedResponse,
    ResponseValidationError,
    Source,
    SourceRequest,
    latest_forecast_day,
    require_data_list,
)
from ukpower.timeutils import decision_time_utc, settlement_day_bounds_utc

if TYPE_CHECKING:
    from ukpower.ingestion.http_client import HttpClient

logger = logging.getLogger(__name__)

ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"


def parse_utc(value: str) -> datetime:
    """Parse an Elexon UTC timestamp such as 2026-10-04T07:45:00Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ElexonAsOfForecast(Source):
    """Partition = target settlement date. One to a few requests per day."""

    #: Endpoint path after the base URL. Must accept publishTime.
    path: ClassVar[str]
    #: Length of one forecast time slot.
    resolution: ClassVar[timedelta]
    #: Extra query parameters, for example {"boundary": "N"}.
    extra_params: ClassVar[dict[str, str]] = {}
    #: Safety limit on how many issues to step back through for one day.
    max_steps = 12
    #: How far before an issue's publish time to ask for the previous issue.
    step_back = timedelta(seconds=1)

    #: Forecasts as of a past decision time never change, so there is nothing to refetch.
    refetch_recent = False

    def latest_available_day(self, now: datetime, tz: ZoneInfo) -> date:
        return latest_forecast_day(now, self._decision_local, self._tz)

    def __init__(self, base_url: str, tz: ZoneInfo, decision_local: time) -> None:
        self._base_url = base_url
        self._tz = tz
        self._decision_local = decision_local

    def _request(self, day: date, as_of: datetime) -> SourceRequest:
        return SourceRequest(
            partition=day.isoformat(),
            url=f"{self._base_url}{self.path}",
            params={"publishTime": as_of.strftime(ISO_UTC), **self.extra_params, "format": "json"},
        )

    def expected_starts(self, day: date) -> set[datetime]:
        """Every time slot start (UTC) in the local settlement day."""
        start, end = settlement_day_bounds_utc(day, self._tz)
        slots = set()
        current = start
        while current < end:
            slots.add(current)
            current += self.resolution
        return slots

    def requests_for_day(self, day: date) -> list[SourceRequest]:
        """The first request: the forecast as it stood at the decision time."""
        return [self._request(day, decision_time_utc(day, self._decision_local, self._tz))]

    def check_records(self, records: list[dict[str, Any]]) -> None:
        """Source specific checks. Override to add more."""

    def validate(self, request: SourceRequest, payload: Any) -> int:  # noqa: ANN401
        records = require_data_list(payload)
        if not records:
            return 0

        publish_times = {r.get("publishTime") for r in records}
        if len(publish_times) != 1:
            raise ResponseValidationError(
                f"{self.name}: expected one issue, "
                f"got publish times {sorted(map(str, publish_times))}"
            )
        published = parse_utc(str(publish_times.pop()))
        as_of = parse_utc(str(request.params["publishTime"]))
        if published > as_of:
            raise ResponseValidationError(
                f"{self.name}: leakage, issue published {published} after as of time {as_of}"
            )

        starts = Counter(r.get("startTime") for r in records)
        duplicates = sorted(str(s) for s, n in starts.items() if n > 1)
        if duplicates:
            raise ResponseValidationError(f"{self.name}: duplicate start times {duplicates[:5]}")

        self.check_records(records)
        return len(records)

    def fetch_day(self, client: HttpClient, day: date) -> list[FetchedResponse]:
        """Step back through issues until every time slot of the target day is covered."""
        expected = self.expected_starts(day)
        covered: set[datetime] = set()
        fetched: list[FetchedResponse] = []
        request = self.requests_for_day(day)[0]

        for _ in range(self.max_steps):
            result = client.get(request.url, request.params)
            payload = result.json()
            count = self.validate(request, payload)
            fetched.append(FetchedResponse(request=request, result=result, record_count=count))

            records = payload["data"]
            if not records:
                logger.warning(
                    "%s %s: no issue before %s", self.name, day, request.params["publishTime"]
                )
                break

            covered |= {parse_utc(r["startTime"]) for r in records} & expected
            if covered >= expected:
                break

            previous_as_of = parse_utc(records[0]["publishTime"]) - self.step_back
            request = self._request(day, previous_as_of)

        if covered >= expected:
            logger.info(
                "%s %s: covered all %d slots in %d requests",
                self.name,
                day,
                len(expected),
                len(fetched),
            )
        else:
            logger.warning(
                "%s %s: covered %d of %d slots after %d requests",
                self.name,
                day,
                len(covered),
                len(expected),
                len(fetched),
            )
        return fetched


class ElexonDemandForecast(ElexonAsOfForecast):
    """Day ahead national demand forecast (NDF, TSDF). Half hourly."""

    name = "elexon_demand_forecast"
    path = "/forecast/demand/day-ahead/history"
    resolution = timedelta(minutes=30)
    extra_params: ClassVar[dict[str, str]] = {"boundary": "N"}

    def check_records(self, records: list[dict[str, Any]]) -> None:
        boundaries = {r.get("boundary") for r in records}
        if boundaries != {"N"}:
            raise ResponseValidationError(f"{self.name}: unexpected boundaries {boundaries}")


class ElexonWindForecast(ElexonAsOfForecast):
    """National wind generation forecast (WINDFOR). Hourly."""

    name = "elexon_wind_forecast"
    path = "/forecast/generation/wind/history"
    resolution = timedelta(hours=1)
