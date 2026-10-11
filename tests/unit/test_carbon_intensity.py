"""Tests for the carbon intensity adapter, using a real response from a clock change day."""

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from ukpower.ingestion.sources.base import ResponseValidationError
from ukpower.ingestion.sources.carbon_intensity import CarbonIntensity

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BASE_URL = "https://api.carbonintensity.org.uk"
UK = ZoneInfo("Europe/London")
CLOCK_CHANGE_DAY = date(2025, 10, 26)


@pytest.fixture
def source() -> CarbonIntensity:
    return CarbonIntensity(BASE_URL, UK)


@pytest.fixture
def payload() -> dict[str, Any]:
    return json.loads((FIXTURES / "carbon_intensity_2025-10-26.json").read_text(encoding="utf-8"))


def validate(source: CarbonIntensity, payload: dict[str, Any]) -> int:
    return source.validate(source.requests_for_day(CLOCK_CHANGE_DAY)[0], payload)


# Requests


def test_summer_day_request_runs_23_00_to_23_00_utc(source: CarbonIntensity) -> None:
    request = source.requests_for_day(date(2026, 10, 5))[0]
    assert request.partition == "2026-10-05"
    assert request.url == f"{BASE_URL}/intensity/2026-10-04T23:00Z/2026-10-05T23:00Z"
    assert request.params == {}


def test_clock_change_day_request_covers_25_hours(source: CarbonIntensity) -> None:
    request = source.requests_for_day(CLOCK_CHANGE_DAY)[0]
    assert request.url == f"{BASE_URL}/intensity/2025-10-25T23:00Z/2025-10-27T00:00Z"


# Real response passes


def test_real_clock_change_day_has_50_windows_plus_one(
    source: CarbonIntensity, payload: dict[str, Any]
) -> None:
    assert validate(source, payload) == 51


# Broken responses fail


def test_window_not_30_minutes_is_rejected(
    source: CarbonIntensity, payload: dict[str, Any]
) -> None:
    broken = copy.deepcopy(payload)
    broken["data"][5]["to"] = broken["data"][6]["to"]  # now 60 minutes long
    with pytest.raises(ResponseValidationError, match="not 30 minutes"):
        validate(source, broken)


def test_window_outside_day_is_rejected(source: CarbonIntensity, payload: dict[str, Any]) -> None:
    broken = copy.deepcopy(payload)
    broken["data"].append(
        {"from": "2025-10-27T00:00Z", "to": "2025-10-27T00:30Z", "intensity": {"actual": 100}}
    )
    with pytest.raises(ResponseValidationError, match="outside"):
        validate(source, broken)


def test_duplicate_window_is_rejected(source: CarbonIntensity, payload: dict[str, Any]) -> None:
    broken = copy.deepcopy(payload)
    broken["data"].append(copy.deepcopy(broken["data"][10]))
    with pytest.raises(ResponseValidationError, match="duplicate windows"):
        validate(source, broken)


def test_missing_window_is_rejected(source: CarbonIntensity, payload: dict[str, Any]) -> None:
    broken = copy.deepcopy(payload)
    del broken["data"][20]
    with pytest.raises(ResponseValidationError, match="1 windows of 2025-10-26 missing"):
        validate(source, broken)


def test_window_without_intensity_is_rejected(
    source: CarbonIntensity, payload: dict[str, Any]
) -> None:
    broken = copy.deepcopy(payload)
    del broken["data"][3]["intensity"]
    with pytest.raises(ResponseValidationError, match="has no intensity"):
        validate(source, broken)


def test_unreadable_time_is_rejected(source: CarbonIntensity, payload: dict[str, Any]) -> None:
    broken = copy.deepcopy(payload)
    broken["data"][0]["from"] = "yesterday"
    with pytest.raises(ResponseValidationError, match="bad window"):
        validate(source, broken)
