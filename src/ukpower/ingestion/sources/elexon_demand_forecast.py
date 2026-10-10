"""Elexon day ahead demand forecast (NDF, TSDF), as known at the decision time.

Two kinds of issue exist: a long daily issue (seen at 07:45 UTC) and short
updates about every 30 minutes that only reach the early hours of the next
day. No single request gives the latest forecast for every period, so we step
back through issues until every period of the target day is covered.
See docs/data_sources.md.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from ukpower.ingestion.sources.base import (
    FetchedResponse,
    ResponseValidationError,
    Source,
    SourceRequest,
    require_data_list,
)
from ukpower.timeutils import decision_time_utc, settlement_periods_in_day

if TYPE_CHECKING:
    from ukpower.ingestion.http_client import HttpClient

logger = logging.getLogger(__name__)

ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"


def parse_utc(value: str) -> datetime:
    """Parse an Elexon UTC timestamp such as 2026-10-04T07:45:00Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ElexonDemandForecast(Source):
    """Partition = target settlement date. One to a few requests per day."""

    name = "elexon_demand_forecast"
    path = "/forecast/demand/day-ahead/history"
    #: Safety limit on how many issues to step back through for one day.
    max_steps = 12
    #: How far before an issue's publish time to ask for the previous issue.
    step_back = timedelta(seconds=1)

    def __init__(self, base_url: str, tz: ZoneInfo, decision_local: time) -> None:
        self._base_url = base_url
        self._tz = tz
        self._decision_local = decision_local

    def _request(self, day: date, as_of: datetime) -> SourceRequest:
        return SourceRequest(
            partition=day.isoformat(),
            url=f"{self._base_url}{self.path}",
            params={"publishTime": as_of.strftime(ISO_UTC), "boundary": "N", "format": "json"},
        )

    def requests_for_day(self, day: date) -> list[SourceRequest]:
        """The first request: the forecast as it stood at the decision time."""
        return [self._request(day, decision_time_utc(day, self._decision_local, self._tz))]

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

        boundaries = {r.get("boundary") for r in records}
        if boundaries != {"N"}:
            raise ResponseValidationError(f"{self.name}: unexpected boundaries {boundaries}")

        starts = Counter(r.get("startTime") for r in records)
        duplicates = sorted(str(s) for s, n in starts.items() if n > 1)
        if duplicates:
            raise ResponseValidationError(f"{self.name}: duplicate start times {duplicates[:5]}")

        return len(records)

    def fetch_day(self, client: HttpClient, day: date) -> list[FetchedResponse]:
        """Step back through issues until every period of the target day is covered."""
        target = day.isoformat()
        needed = settlement_periods_in_day(day, self._tz)
        covered: set[int] = set()
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
                    "%s %s: no issue before %s", self.name, target, request.params["publishTime"]
                )
                break

            covered |= {r["settlementPeriod"] for r in records if r.get("settlementDate") == target}
            if len(covered) >= needed:
                break

            previous_as_of = parse_utc(records[0]["publishTime"]) - self.step_back
            request = self._request(day, previous_as_of)

        if len(covered) < needed:
            logger.warning(
                "%s %s: covered %d of %d periods after %d requests",
                self.name,
                target,
                len(covered),
                needed,
                len(fetched),
            )
        else:
            logger.info(
                "%s %s: covered all %d periods in %d requests",
                self.name,
                target,
                needed,
                len(fetched),
            )
        return fetched
