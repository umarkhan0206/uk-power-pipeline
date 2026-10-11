"""Tests for the shared as of forecast logic and the wind forecast adapter."""

from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import responses
from responses import matchers

from ukpower.config import HttpSettings
from ukpower.ingestion.http_client import HttpClient
from ukpower.ingestion.sources.elexon_forecasts import (
    ElexonAsOfForecast,
    ElexonDemandForecast,
    ElexonWindForecast,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"
WIND_URL = f"{BASE_URL}/forecast/generation/wind/history"
UK = ZoneInfo("Europe/London")


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


def make(source_cls: type[ElexonAsOfForecast]) -> ElexonAsOfForecast:
    return source_cls(BASE_URL, UK, time(10, 0))


# Time slots per day, including clock changes


@pytest.mark.parametrize(
    ("source_cls", "day", "expected"),
    [
        (ElexonDemandForecast, date(2026, 10, 5), 48),
        (ElexonDemandForecast, date(2025, 10, 26), 50),
        (ElexonDemandForecast, date(2026, 3, 29), 46),
        (ElexonWindForecast, date(2026, 10, 5), 24),
        (ElexonWindForecast, date(2025, 10, 26), 25),
        (ElexonWindForecast, date(2026, 3, 29), 23),
    ],
)
def test_expected_slots_per_day(
    source_cls: type[ElexonAsOfForecast], day: date, expected: int
) -> None:
    assert len(make(source_cls).expected_starts(day)) == expected


# Requests


def test_wind_request_has_no_boundary_parameter() -> None:
    request = make(ElexonWindForecast).requests_for_day(date(2026, 10, 5))[0]
    assert request.url == WIND_URL
    assert request.params == {"publishTime": "2026-10-04T09:00:00Z", "format": "json"}


def test_decision_before_autumn_clock_change_is_still_summer_time() -> None:
    # The decision for 26 Oct 2025 is on 25 Oct, before clocks went back: 10:00 BST = 09:00 UTC.
    request = make(ElexonWindForecast).requests_for_day(date(2025, 10, 26))[0]
    assert request.params["publishTime"] == "2025-10-25T09:00:00Z"


# Real responses


@pytest.mark.parametrize(
    ("day", "as_of", "stamp", "expected_slots"),
    [
        (date(2026, 10, 5), "2026-10-04T09:00:00Z", "20261004T090000Z", 24),
        (date(2025, 10, 26), "2025-10-25T09:00:00Z", "20251025T090000Z", 25),
    ],
)
@responses.activate
def test_real_wind_issue_covers_whole_day_in_one_request(
    client: HttpClient, day: date, as_of: str, stamp: str, expected_slots: int
) -> None:
    body = (FIXTURES / f"elexon_wind_forecast_{day}_asof_{stamp}.json").read_bytes()
    responses.add(
        responses.GET,
        WIND_URL,
        body=body,
        match=[matchers.query_param_matcher({"publishTime": as_of, "format": "json"})],
    )
    source = make(ElexonWindForecast)

    fetched = source.fetch_day(client, day)

    assert len(fetched) == 1
    assert fetched[0].result.json()["data"][0]["publishTime"] <= as_of
    starts = {r["startTime"] for r in fetched[0].result.json()["data"]}
    expected = {s.strftime("%Y-%m-%dT%H:%M:%SZ") for s in source.expected_starts(day)}
    assert expected <= starts
    assert len(expected) == expected_slots
