"""Tests for the Open-Meteo ECMWF adapter, using a real response from a clock change day."""

import copy
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest

from ukpower.config import OpenMeteoSettings, load_settings
from ukpower.ingestion.sources.base import ResponseValidationError, SourceRequest
from ukpower.ingestion.sources.open_meteo import OpenMeteoForecast

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CLOCK_CHANGE_DAY = date(2025, 10, 26)


@pytest.fixture
def settings() -> OpenMeteoSettings:
    return load_settings().open_meteo


@pytest.fixture
def source(settings: OpenMeteoSettings) -> OpenMeteoForecast:
    s = load_settings()
    return OpenMeteoForecast(settings, s.tz, time(10, 0))


@pytest.fixture
def payload() -> list[dict[str, Any]]:
    path = FIXTURES / "open_meteo_ecmwf_2025-10-26.json"
    return json.loads(path.read_text(encoding="utf-8"))


def request_for(source: OpenMeteoForecast, day: date = CLOCK_CHANGE_DAY) -> Any:  # noqa: ANN401
    return source.requests_for_day(day)[0]


# Which run, and the leakage guard


def test_uses_midnight_run_on_the_day_before(source: OpenMeteoForecast) -> None:
    assert source.run_for_day(date(2026, 10, 5)) == datetime(2026, 10, 4, 0, 0, tzinfo=UTC)


def test_run_too_late_for_decision_is_refused(settings: OpenMeteoSettings) -> None:
    # Decision at 05:00 UK on 4 Oct is 04:00 UTC: the 00:00 run is assumed ready only at 06:00.
    early = OpenMeteoForecast(settings, load_settings().tz, time(5, 0))
    with pytest.raises(ValueError, match="not assumed available"):
        early.run_for_day(date(2026, 10, 5))


# Requests


def test_one_request_for_all_locations(
    source: OpenMeteoForecast, settings: OpenMeteoSettings
) -> None:
    request = request_for(source, date(2026, 10, 5))
    n = len(settings.locations)

    assert request.partition == "2026-10-05"
    assert request.params["run"] == "2026-10-04T00:00"
    assert request.params["timezone"] == "GMT"
    assert len(str(request.params["latitude"]).split(",")) == n
    assert len(str(request.params["longitude"]).split(",")) == n


@pytest.mark.parametrize(
    ("day", "hours"),
    [(date(2026, 10, 5), 24), (date(2025, 10, 26), 25), (date(2026, 3, 29), 23)],
)
def test_target_hours_follow_clock_changes(
    source: OpenMeteoForecast, day: date, hours: int
) -> None:
    assert len(source.target_hours(day)) == hours


# Real response passes


def test_real_clock_change_response_validates(
    source: OpenMeteoForecast, payload: list[dict[str, Any]], settings: OpenMeteoSettings
) -> None:
    expected = len(settings.locations) * len(payload[0]["hourly"]["time"])
    assert source.validate(request_for(source), payload) == expected


# Broken responses fail


def test_missing_location_is_rejected(
    source: OpenMeteoForecast, payload: list[dict[str, Any]]
) -> None:
    with pytest.raises(ResponseValidationError, match="expected .* locations"):
        source.validate(request_for(source), payload[:-1])


def test_point_far_from_request_is_rejected(
    source: OpenMeteoForecast, payload: list[dict[str, Any]]
) -> None:
    broken = copy.deepcopy(payload)
    broken[0]["latitude"] += 1.0
    with pytest.raises(ResponseValidationError, match="requested"):
        source.validate(request_for(source), broken)


def test_missing_variable_is_rejected(
    source: OpenMeteoForecast, payload: list[dict[str, Any]]
) -> None:
    broken = copy.deepcopy(payload)
    del broken[0]["hourly"]["shortwave_radiation"]
    with pytest.raises(ResponseValidationError, match="missing time or variables"):
        source.validate(request_for(source), broken)


def test_unequal_series_lengths_are_rejected(
    source: OpenMeteoForecast, payload: list[dict[str, Any]]
) -> None:
    broken = copy.deepcopy(payload)
    broken[0]["hourly"]["temperature_2m"].pop()
    with pytest.raises(ResponseValidationError, match="unequal series lengths"):
        source.validate(request_for(source), broken)


def test_forecast_not_covering_target_day_is_rejected(
    source: OpenMeteoForecast, payload: list[dict[str, Any]]
) -> None:
    broken = copy.deepcopy(payload)
    for item in broken:
        for key in item["hourly"]:
            item["hourly"][key] = item["hourly"][key][:30]  # stops well before the day ends
    with pytest.raises(ResponseValidationError, match="does not cover"):
        source.validate(request_for(source), broken)


def test_single_location_response_object_is_accepted(
    settings: OpenMeteoSettings, payload: list[dict[str, Any]]
) -> None:
    one = settings.model_copy(update={"locations": settings.locations[:1]})
    source = OpenMeteoForecast(one, load_settings().tz, time(10, 0))
    assert source.validate(request_for(source), payload[0]) == len(payload[0]["hourly"]["time"])


def _om_source_and_payload() -> tuple[OpenMeteoForecast, SourceRequest, list]:
    settings = load_settings()
    source = OpenMeteoForecast(settings.open_meteo, settings.tz, settings.decision_time_local)
    request = source.requests_for_day(date(2025, 10, 26))[0]
    path = Path(__file__).resolve().parents[1] / "fixtures" / "open_meteo_ecmwf_2025-10-26.json"
    return source, request, json.loads(path.read_text(encoding="utf-8"))


def test_hollow_run_is_rejected() -> None:
    # Real case: target day 2025-08-08 had the right shape but every value was null
    source, request, payload = _om_source_and_payload()
    hourly = payload[0]["hourly"]
    hourly["temperature_2m"] = [None] * len(hourly["time"])
    with pytest.raises(ResponseValidationError, match="missing values for temperature_2m"):
        source.validate(request, payload)


def test_single_blank_target_hour_is_rejected() -> None:
    source, request, payload = _om_source_and_payload()
    hourly = payload[3]["hourly"]
    target = sorted(source.target_hours(date(2025, 10, 26)))[10]
    hourly["wind_speed_100m"][hourly["time"].index(target)] = None
    with pytest.raises(ResponseValidationError, match="1 missing values for wind_speed_100m"):
        source.validate(request, payload)
