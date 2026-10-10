"""Tests for the system prices adapter, using real Elexon responses as fixtures."""

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from ukpower.ingestion.sources.base import (
    ResponseValidationError,
    Source,
    SourceRequest,
    require_data_list,
)
from ukpower.ingestion.sources.elexon_system_prices import ElexonSystemPrices

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"
UK = ZoneInfo("Europe/London")


def load_fixture(day: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"elexon_system_prices_{day}.json").read_text(encoding="utf-8"))


@pytest.fixture
def source() -> ElexonSystemPrices:
    return ElexonSystemPrices(BASE_URL, UK)


def request_for(source: ElexonSystemPrices, day: date) -> SourceRequest:
    return source.requests_for_day(day)[0]


# Requests


def test_one_request_per_day_with_expected_url(source: ElexonSystemPrices) -> None:
    requests = source.requests_for_day(date(2026, 10, 6))

    assert len(requests) == 1
    assert requests[0].partition == "2026-10-06"
    assert requests[0].url == f"{BASE_URL}/balancing/settlement/system-prices/2026-10-06"
    assert requests[0].params == {"format": "json"}


# Real responses pass


def test_real_normal_day_has_48_records(source: ElexonSystemPrices) -> None:
    req = request_for(source, date(2026, 10, 6))
    assert source.validate(req, load_fixture("2026-10-06")) == 48


def test_real_clock_change_day_has_50_records(source: ElexonSystemPrices) -> None:
    req = request_for(source, date(2025, 10, 26))
    assert source.validate(req, load_fixture("2025-10-26")) == 50


def test_partial_day_is_accepted(source: ElexonSystemPrices) -> None:
    # Recent days may still be arriving. Completeness is checked later, not here.
    payload = load_fixture("2026-10-06")
    payload["data"] = payload["data"][:10]
    req = request_for(source, date(2026, 10, 6))
    assert source.validate(req, payload) == 10


# Broken responses fail


def test_period_49_on_a_normal_day_is_rejected(source: ElexonSystemPrices) -> None:
    payload = load_fixture("2026-10-06")
    payload["data"][0]["settlementPeriod"] = 49
    req = request_for(source, date(2026, 10, 6))

    with pytest.raises(ResponseValidationError, match="invalid settlement periods"):
        source.validate(req, payload)


def test_records_for_another_date_are_rejected(source: ElexonSystemPrices) -> None:
    payload = load_fixture("2026-10-06")
    payload["data"][0]["settlementDate"] = "2026-10-05"
    req = request_for(source, date(2026, 10, 6))

    with pytest.raises(ResponseValidationError, match="unexpected dates"):
        source.validate(req, payload)


def test_duplicate_periods_are_rejected(source: ElexonSystemPrices) -> None:
    payload = load_fixture("2026-10-06")
    payload["data"].append(copy.deepcopy(payload["data"][0]))
    req = request_for(source, date(2026, 10, 6))

    with pytest.raises(ResponseValidationError, match="duplicate settlement periods"):
        source.validate(req, payload)


@pytest.mark.parametrize(
    "payload",
    [{}, {"data": "oops"}, {"data": [1, 2]}, [], None],
)
def test_malformed_payloads_are_rejected(source: ElexonSystemPrices, payload: Any) -> None:  # noqa: ANN401
    req = request_for(source, date(2026, 10, 6))
    with pytest.raises(ResponseValidationError):
        source.validate(req, payload)


# Shared helpers and interface


def test_require_data_list_returns_records() -> None:
    assert require_data_list({"data": [{"a": 1}]}) == [{"a": 1}]


def test_source_without_methods_cannot_be_created() -> None:
    class Incomplete(Source):
        name = "incomplete"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]
