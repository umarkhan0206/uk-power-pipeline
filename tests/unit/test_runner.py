"""Tests for the ingestion runner, using a fake source we fully control."""

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests

from ukpower.config import HttpSettings
from ukpower.ingestion.http_client import FetchResult, HttpClient, HttpError
from ukpower.ingestion.landing import LandingZone
from ukpower.ingestion.runner import ingest_day, run
from ukpower.ingestion.sources.base import (
    FetchedResponse,
    ResponseValidationError,
    Source,
    SourceRequest,
)

DAY_1, DAY_2, DAY_3 = date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)


class FakeSource(Source):
    """Returns canned responses, or raises a chosen error on chosen days."""

    name = "fake_source"

    def __init__(self, fail_on: dict[date, Exception] | None = None, responses: int = 1) -> None:
        self.fail_on = fail_on or {}
        self.responses = responses
        self.calls: list[date] = []

    def requests_for_day(self, day: date) -> list[SourceRequest]:
        return [SourceRequest(partition=day.isoformat(), url="https://api.example.test/x")]

    def validate(self, request: SourceRequest, payload: Any) -> int:  # noqa: ANN401
        return 0

    def fetch_day(self, client: HttpClient, day: date) -> list[FetchedResponse]:
        self.calls.append(day)
        if day in self.fail_on:
            raise self.fail_on[day]
        request = self.requests_for_day(day)[0]
        base = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
        return [
            FetchedResponse(
                request=request,
                result=FetchResult(
                    url=request.url,
                    status_code=200,
                    fetched_at=base + timedelta(microseconds=i),
                    content=f'{{"data": [{i}]}}'.encode(),
                ),
                record_count=10,
            )
            for i in range(self.responses)
        ]


@pytest.fixture
def client() -> HttpClient:
    settings = HttpSettings(
        timeout_seconds=5,
        max_retries=0,
        backoff_base_seconds=0.01,
        backoff_max_seconds=1,
        requests_per_second=10,
    )
    return HttpClient(settings, sleep=lambda _: None)


@pytest.fixture
def landing(tmp_path: Path) -> LandingZone:
    return LandingZone(tmp_path / "landing")


# Successful days


def test_successful_day_is_landed_and_counted(client: HttpClient, landing: LandingZone) -> None:
    outcome = ingest_day(FakeSource(responses=3), DAY_1, client, landing)

    assert outcome.status == "landed"
    assert outcome.responses == 3
    assert outcome.records == 30
    assert len(landing.list_data_files("fake_source", "2025-01-01")) == 3


# Failures do not stop the run


def test_failed_day_does_not_stop_later_days(client: HttpClient, landing: LandingZone) -> None:
    source = FakeSource(fail_on={DAY_2: ResponseValidationError("bad response")})

    summary = run([source], [DAY_1, DAY_2, DAY_3], client, landing)

    assert source.calls == [DAY_1, DAY_2, DAY_3]
    assert [o.day for o in summary.landed] == [DAY_1, DAY_3]
    assert [o.day for o in summary.failed] == [DAY_2]
    assert "bad response" in (summary.failed[0].error or "")


def test_failed_day_lands_nothing(client: HttpClient, landing: LandingZone) -> None:
    source = FakeSource(fail_on={DAY_2: ResponseValidationError("bad")}, responses=3)
    run([source], [DAY_2], client, landing)
    assert landing.list_data_files("fake_source", "2025-01-02") == []


@pytest.mark.parametrize(
    "error",
    [
        HttpError(404, "https://api.example.test/x", "not found"),
        requests.ConnectionError("network down"),
        requests.Timeout("too slow"),
        ResponseValidationError("bad shape"),
        ValueError("not JSON"),
    ],
)
def test_expected_failures_are_recorded_not_raised(
    client: HttpClient, landing: LandingZone, error: Exception
) -> None:
    outcome = ingest_day(FakeSource(fail_on={DAY_1: error}), DAY_1, client, landing)
    assert outcome.status == "failed"
    assert type(error).__name__ in (outcome.error or "")


# Bugs are not hidden


def test_unexpected_error_is_raised(client: HttpClient, landing: LandingZone) -> None:
    source = FakeSource(fail_on={DAY_1: KeyError("settlementPeriod")})
    with pytest.raises(KeyError):
        run([source], [DAY_1, DAY_2], client, landing)


# Day handling and summary


def test_days_are_deduplicated_and_processed_oldest_first(
    client: HttpClient, landing: LandingZone
) -> None:
    source = FakeSource()
    run([source], [DAY_3, DAY_1, DAY_2, DAY_1], client, landing)
    assert source.calls == [DAY_1, DAY_2, DAY_3]


def test_summary_log_reports_failures(
    client: HttpClient, landing: LandingZone, caplog: pytest.LogCaptureFixture
) -> None:
    source = FakeSource(fail_on={DAY_2: ResponseValidationError("bad response")})
    summary = run([source], [DAY_1, DAY_2], client, landing)

    with caplog.at_level(logging.INFO):
        summary.log()

    assert "fake_source: 1 landed, 1 failed" in caplog.text
    assert "FAILED fake_source 2025-01-02" in caplog.text
