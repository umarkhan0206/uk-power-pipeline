"""Run sources over days: fetch, validate and land, carrying on past expected failures.

Each (source, day) is all or nothing: responses are landed only if every response
for that day was fetched and validated. Expected failures (network, HTTP errors,
invalid responses) are logged and recorded; anything else is a bug and is raised.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import requests

from ukpower.ingestion.http_client import HttpClient, HttpError
from ukpower.ingestion.landing import LandingZone
from ukpower.ingestion.sources.base import ResponseValidationError, Source

logger = logging.getLogger(__name__)

#: Failures that happen in normal operation and should not stop a run.
EXPECTED_FAILURES: tuple[type[Exception], ...] = (
    HttpError,  # includes RetryableHttpError after retries run out
    requests.RequestException,  # connection errors and timeouts after retries
    ResponseValidationError,
    ValueError,  # body is not valid JSON, or a run is too late for the decision time
)


@dataclass(frozen=True)
class DayOutcome:
    """What happened for one source on one day."""

    source: str
    day: date
    status: Literal["landed", "failed"]
    responses: int = 0
    records: int = 0
    error: str | None = None


@dataclass
class RunSummary:
    """All outcomes of a run."""

    outcomes: list[DayOutcome] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def failed(self) -> list[DayOutcome]:
        return [o for o in self.outcomes if o.status == "failed"]

    @property
    def landed(self) -> list[DayOutcome]:
        return [o for o in self.outcomes if o.status == "landed"]

    def log(self) -> None:
        """Write a per source summary, then every failure."""
        landed = Counter(o.source for o in self.landed)
        failed = Counter(o.source for o in self.failed)
        for source in sorted({o.source for o in self.outcomes}):
            logger.info("%s: %d landed, %d failed", source, landed[source], failed[source])
        for o in self.failed:
            logger.error("FAILED %s %s: %s", o.source, o.day, o.error)
        logger.info(
            "Run finished in %.0f s: %d landed, %d failed",
            self.seconds,
            len(self.landed),
            len(self.failed),
        )


def ingest_day(source: Source, day: date, client: HttpClient, landing: LandingZone) -> DayOutcome:
    """Fetch, validate and land one source for one day."""
    try:
        fetched = source.fetch_day(client, day)
    except EXPECTED_FAILURES as exc:
        logger.error("%s %s failed: %s", source.name, day, exc)
        return DayOutcome(source.name, day, "failed", error=f"{type(exc).__name__}: {exc}")

    for item in fetched:
        landing.write(source.name, item.request.partition, item.result, item.request.params)
    records = sum(item.record_count for item in fetched)
    return DayOutcome(source.name, day, "landed", responses=len(fetched), records=records)


def run(
    sources: Iterable[Source],
    days: Iterable[date],
    client: HttpClient,
    landing: LandingZone,
) -> RunSummary:
    """Ingest every source for every day, oldest day first, and summarise."""
    started = time.monotonic()
    day_list = sorted(set(days))
    summary = RunSummary()

    for source in sources:
        logger.info("%s: %d days to ingest", source.name, len(day_list))
        for i, day in enumerate(day_list, start=1):
            summary.outcomes.append(ingest_day(source, day, client, landing))
            if i % 50 == 0:
                logger.info("%s: %d of %d days done", source.name, i, len(day_list))

    summary.seconds = time.monotonic() - started
    return summary
