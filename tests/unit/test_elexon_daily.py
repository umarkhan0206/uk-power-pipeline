"""Tests for the shared daily Elexon adapter and its three datasets, using real fixtures."""

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from ukpower.ingestion.sources.base import ResponseValidationError
from ukpower.ingestion.sources.elexon_daily import (
    ElexonDailySource,
    ElexonDemandOutturn,
    ElexonGenerationByFuel,
    ElexonWindSolarActual,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"
UK = ZoneInfo("Europe/London")
CLOCK_CHANGE_DAY = date(2025, 10, 26)

# (source class, records expected in the real 2025-10-26 response)
CASES = [
    (ElexonDemandOutturn, 50),
    (ElexonGenerationByFuel, 1000),
    (ElexonWindSolarActual, 150),
]


def make(source_cls: type[ElexonDailySource]) -> ElexonDailySource:
    return source_cls(BASE_URL, UK)


def load_fixture(source: ElexonDailySource) -> dict[str, Any]:
    path = FIXTURES / f"{source.name}_{CLOCK_CHANGE_DAY.isoformat()}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# Real responses pass


@pytest.mark.parametrize(("source_cls", "expected"), CASES)
def test_real_clock_change_day_validates(
    source_cls: type[ElexonDailySource], expected: int
) -> None:
    source = make(source_cls)
    request = source.requests_for_day(CLOCK_CHANGE_DAY)[0]
    assert source.validate(request, load_fixture(source)) == expected


# Requests


def test_demand_requests_one_settlement_date() -> None:
    request = make(ElexonDemandOutturn).requests_for_day(CLOCK_CHANGE_DAY)[0]
    assert request.url == f"{BASE_URL}/demand/outturn"
    assert request.params == {
        "settlementDateFrom": "2025-10-26",
        "settlementDateTo": "2025-10-26",
        "format": "json",
    }


def test_fuel_requests_one_settlement_date() -> None:
    request = make(ElexonGenerationByFuel).requests_for_day(CLOCK_CHANGE_DAY)[0]
    assert request.url == f"{BASE_URL}/datasets/FUELHH"
    assert request.params["settlementDateFrom"] == "2025-10-26"
    assert request.params["settlementDateTo"] == "2025-10-26"


def test_wind_solar_requests_cover_all_possible_periods() -> None:
    request = make(ElexonWindSolarActual).requests_for_day(CLOCK_CHANGE_DAY)[0]
    assert request.url == f"{BASE_URL}/generation/actual/per-type/wind-and-solar"
    assert request.params["settlementPeriodFrom"] == 1
    assert request.params["settlementPeriodTo"] == 50


def test_partition_is_the_settlement_date() -> None:
    for source_cls, _ in CASES:
        request = make(source_cls).requests_for_day(CLOCK_CHANGE_DAY)[0]
        assert request.partition == "2025-10-26"


# Broken responses fail


@pytest.mark.parametrize(("source_cls", "_expected"), CASES)
def test_wrong_date_is_rejected(source_cls: type[ElexonDailySource], _expected: int) -> None:
    source = make(source_cls)
    payload = load_fixture(source)
    payload["data"][0]["settlementDate"] = "2025-10-25"
    request = source.requests_for_day(CLOCK_CHANGE_DAY)[0]

    with pytest.raises(ResponseValidationError, match="unexpected dates"):
        source.validate(request, payload)


@pytest.mark.parametrize(("source_cls", "_expected"), CASES)
def test_period_51_is_rejected_even_on_clock_change_day(
    source_cls: type[ElexonDailySource], _expected: int
) -> None:
    source = make(source_cls)
    payload = load_fixture(source)
    payload["data"][0]["settlementPeriod"] = 51
    request = source.requests_for_day(CLOCK_CHANGE_DAY)[0]

    with pytest.raises(ResponseValidationError, match="invalid settlement periods"):
        source.validate(request, payload)


@pytest.mark.parametrize(("source_cls", "_expected"), CASES)
def test_exact_duplicate_record_is_rejected(
    source_cls: type[ElexonDailySource], _expected: int
) -> None:
    source = make(source_cls)
    payload = load_fixture(source)
    payload["data"].append(copy.deepcopy(payload["data"][0]))
    request = source.requests_for_day(CLOCK_CHANGE_DAY)[0]

    with pytest.raises(ResponseValidationError, match="duplicate records"):
        source.validate(request, payload)


def test_same_period_different_fuel_is_not_a_duplicate() -> None:
    source = make(ElexonGenerationByFuel)
    payload = load_fixture(source)
    period_one = [r for r in payload["data"] if r["settlementPeriod"] == 1]

    assert len({r["fuelType"] for r in period_one}) > 1
    request = source.requests_for_day(CLOCK_CHANGE_DAY)[0]
    assert source.validate(request, {"data": period_one}) == len(period_one)
