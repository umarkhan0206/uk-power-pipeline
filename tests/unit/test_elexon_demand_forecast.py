"""Tests for the demand forecast adapter, replaying real Elexon responses."""

import json
from datetime import date, time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
import responses
from responses import matchers

from ukpower.config import HttpSettings
from ukpower.ingestion.http_client import HttpClient
from ukpower.ingestion.sources.base import ResponseValidationError, SourceRequest
from ukpower.ingestion.sources.elexon_forecasts import ElexonDemandForecast

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"
URL = f"{BASE_URL}/forecast/demand/day-ahead/history"
UK = ZoneInfo("Europe/London")
TARGET = date(2026, 10, 5)

# The as of times the adapter should ask for, in order, and what came back (real data).
CHAIN = [
    ("2026-10-04T09:00:00Z", "20261004T090000Z", "2026-10-04T08:46:00Z", 38),
    ("2026-10-04T08:45:59Z", "20261004T084559Z", "2026-10-04T08:16:00Z", 39),
    ("2026-10-04T08:15:59Z", "20261004T081559Z", "2026-10-04T07:45:00Z", 48),
]


def fixture_bytes(stamp: str) -> bytes:
    return (FIXTURES / f"elexon_demand_forecast_2026-10-05_asof_{stamp}.json").read_bytes()


@pytest.fixture
def source() -> ElexonDemandForecast:
    return ElexonDemandForecast(BASE_URL, UK, time(10, 0))


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


def register_real_chain() -> None:
    for as_of, stamp, _, _ in CHAIN:
        responses.add(
            responses.GET,
            URL,
            body=fixture_bytes(stamp),
            match=[
                matchers.query_param_matcher(
                    {"publishTime": as_of, "boundary": "N", "format": "json"}
                )
            ],
        )


# The real chain


def test_first_request_is_as_of_decision_time_in_utc(source: ElexonDemandForecast) -> None:
    request = source.requests_for_day(TARGET)[0]
    assert request.partition == "2026-10-05"
    assert request.params["publishTime"] == "2026-10-04T09:00:00Z"  # 10:00 BST


@responses.activate
def test_steps_back_through_real_issues_until_day_is_covered(
    source: ElexonDemandForecast, client: HttpClient
) -> None:
    register_real_chain()

    fetched = source.fetch_day(client, TARGET)

    assert [f.request.params["publishTime"] for f in fetched] == [c[0] for c in CHAIN]
    assert [f.result.json()["data"][0]["publishTime"] for f in fetched] == [c[2] for c in CHAIN]
    assert [f.record_count for f in fetched] == [c[3] for c in CHAIN]


@responses.activate
def test_real_chain_covers_every_period_of_target_day(
    source: ElexonDemandForecast, client: HttpClient
) -> None:
    register_real_chain()

    fetched = source.fetch_day(client, TARGET)
    covered = {
        r["settlementPeriod"]
        for f in fetched
        for r in f.result.json()["data"]
        if r["settlementDate"] == "2026-10-05"
    }

    assert covered == set(range(1, 49))


# Stopping conditions


@responses.activate
def test_stops_when_no_earlier_issue_exists(
    source: ElexonDemandForecast, client: HttpClient
) -> None:
    responses.add(responses.GET, URL, json={"data": []})

    fetched = source.fetch_day(client, TARGET)

    assert len(fetched) == 1
    assert fetched[0].record_count == 0


@responses.activate
def test_stops_at_max_steps_if_day_never_fully_covered(
    source: ElexonDemandForecast, client: HttpClient
) -> None:
    def short_update(request: Any) -> tuple[int, dict[str, str], str]:  # noqa: ANN401
        # Always reply with an issue 10 minutes older than asked, covering only period 1.
        as_of = parse_qs(urlparse(request.url).query)["publishTime"][0]
        minute = int(as_of[14:16])
        published = f"{as_of[:14]}{max(minute - 10, 0):02d}:00Z"
        record = {
            "startTime": "2026-10-04T23:00:00Z",
            "settlementDate": "2026-10-05",
            "settlementPeriod": 1,
            "boundary": "N",
            "publishTime": published,
            "nationalDemand": 20000,
            "transmissionSystemDemand": 21000,
        }
        return 200, {}, json.dumps({"data": [record]})

    responses.add_callback(responses.GET, URL, callback=short_update)
    source.max_steps = 3

    fetched = source.fetch_day(client, TARGET)

    assert len(fetched) == 3


# Validation


def make_request(as_of: str) -> SourceRequest:
    return SourceRequest(
        partition="2026-10-05", url=URL, params={"publishTime": as_of, "boundary": "N"}
    )


def record(
    publish: str, start: str = "2026-10-05T12:00:00Z", boundary: str = "N"
) -> dict[str, Any]:
    return {
        "startTime": start,
        "boundary": boundary,
        "publishTime": publish,
        "settlementDate": "2026-10-05",
    }


def test_issue_published_after_as_of_time_is_rejected(source: ElexonDemandForecast) -> None:
    payload = {"data": [record("2026-10-04T09:30:00Z")]}
    with pytest.raises(ResponseValidationError, match="leakage"):
        source.validate(make_request("2026-10-04T09:00:00Z"), payload)


def test_mixed_issues_in_one_response_are_rejected(source: ElexonDemandForecast) -> None:
    payload = {
        "data": [
            record("2026-10-04T07:45:00Z"),
            record("2026-10-04T08:16:00Z", start="2026-10-05T12:30:00Z"),
        ]
    }
    with pytest.raises(ResponseValidationError, match="expected one issue"):
        source.validate(make_request("2026-10-04T09:00:00Z"), payload)


def test_unexpected_boundary_is_rejected(source: ElexonDemandForecast) -> None:
    payload = {"data": [record("2026-10-04T07:45:00Z", boundary="T")]}
    with pytest.raises(ResponseValidationError, match="unexpected boundaries"):
        source.validate(make_request("2026-10-04T09:00:00Z"), payload)


def test_duplicate_start_times_are_rejected(source: ElexonDemandForecast) -> None:
    payload = {"data": [record("2026-10-04T07:45:00Z"), record("2026-10-04T07:45:00Z")]}
    with pytest.raises(ResponseValidationError, match="duplicate start times"):
        source.validate(make_request("2026-10-04T09:00:00Z"), payload)
