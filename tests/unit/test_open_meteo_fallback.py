"""The weather fallback: newest complete run wins, and only real gaps trigger it."""

import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from ukpower.config import load_settings
from ukpower.ingestion.http_client import FetchResult, HttpError, RetryableHttpError
from ukpower.ingestion.sources.base import ResponseValidationError
from ukpower.ingestion.sources.open_meteo import OpenMeteoForecast

DAY = date(2025, 10, 26)
NORMAL_RUN = "2025-10-25T00:00"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "open_meteo_ecmwf_2025-10-26.json"
NOT_AVAILABLE = '{"error":true,"reason":"The requested model run is not available."}'


def make_source() -> OpenMeteoForecast:
    s = load_settings()
    return OpenMeteoForecast(s.open_meteo, s.tz, s.decision_time_local)


def good_payload() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def hollow_payload() -> list[dict[str, Any]]:
    payload = good_payload()
    payload[0]["hourly"]["temperature_2m"] = [None] * len(payload[0]["hourly"]["time"])
    return payload


class FakeClient:
    """Answers by run time. Runs not listed are 'not available', like the real API."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any]) -> FetchResult:
        self.calls.append(dict(params))
        answer = self.answers.get(params["run"])
        if answer is None:
            raise HttpError(400, url, NOT_AVAILABLE)
        if isinstance(answer, Exception):
            raise answer
        return FetchResult(url, 200, datetime.now(UTC), json.dumps(answer).encode())


def test_normal_run_is_used_when_complete() -> None:
    client = FakeClient({NORMAL_RUN: good_payload()})
    fetched = make_source().fetch_day(client, DAY)
    assert [c["run"] for c in client.calls] == [NORMAL_RUN]
    assert fetched[0].request.params["forecast_days"] == "3"


def test_missing_run_falls_back_to_six_hours_earlier() -> None:
    # Real case: target day 2025-08-09 had no 00:00 run; 12:00 two days earlier was complete
    client = FakeClient({"2025-10-24T18:00": good_payload()})
    fetched = make_source().fetch_day(client, DAY)
    assert [c["run"] for c in client.calls] == [NORMAL_RUN, "2025-10-24T18:00"]
    assert fetched[0].request.params["run"] == "2025-10-24T18:00"
    assert fetched[0].request.params["forecast_days"] == "4"  # one extra day to reach the target


def test_hollow_run_falls_back() -> None:
    # Real case: target day 2025-08-08 had a run with the right shape but blank values
    client = FakeClient({NORMAL_RUN: hollow_payload(), "2025-10-24T12:00": good_payload()})
    fetched = make_source().fetch_day(client, DAY)
    assert fetched[0].request.params["run"] == "2025-10-24T12:00"
    assert len(client.calls) == 3


def test_no_complete_run_within_48_hours_fails_listing_every_attempt() -> None:
    client = FakeClient({})
    with pytest.raises(ResponseValidationError, match="no complete run within 48 h") as err:
        make_source().fetch_day(client, DAY)
    assert len(client.calls) == 9  # the normal run plus 8 earlier ones, 6 h apart
    assert str(err.value).count("not available") == 9


def test_rate_limit_does_not_trigger_fallback() -> None:
    # Busy service is not a missing run: using older data here would be wrong
    busy = RetryableHttpError(429, "https://x", "slow down", retry_after=None)
    client = FakeClient({NORMAL_RUN: busy, "2025-10-24T18:00": good_payload()})
    with pytest.raises(RetryableHttpError):
        make_source().fetch_day(client, DAY)
    assert len(client.calls) == 1


def test_every_candidate_run_is_available_before_the_decision() -> None:
    source = make_source()
    runs = source.candidate_runs(DAY)
    assert len(runs) == 9
    assert runs == sorted(runs, reverse=True)  # newest first
    assert runs[0] == source.run_for_day(DAY)


def test_fixture_is_not_modified_by_tests() -> None:
    before = copy.deepcopy(good_payload())
    hollow_payload()
    assert good_payload() == before
