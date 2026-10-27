"""Tests for the Market Index Data (MID) source."""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from ukpower.ingestion.sources.base import ResponseValidationError, SourceRequest
from ukpower.ingestion.sources.elexon_market_index import ElexonMarketIndex

TZ = ZoneInfo("Europe/London")
BASE = "https://example.test/api"
DAY = date(2026, 10, 5)


@pytest.fixture
def source() -> ElexonMarketIndex:
    return ElexonMarketIndex(BASE, TZ)


def make_payload(day: date = DAY, periods: int = 48) -> dict[str, Any]:
    rows = []
    for p in range(1, periods + 1):
        rows.append(
            {
                "settlementDate": day.isoformat(),
                "settlementPeriod": p,
                "dataProvider": "APXMIDP",
                "price": 100.0 + p,
                "volume": 1800.0,
            }
        )
        rows.append(
            {
                "settlementDate": day.isoformat(),
                "settlementPeriod": p,
                "dataProvider": "N2EXMIDP",
                "price": 0.0,
                "volume": 0.0,
            }
        )
    return {"data": rows}


def request_for(source: ElexonMarketIndex, day: date = DAY) -> SourceRequest:
    (request,) = source.requests_for_day(day)
    return request


@pytest.mark.parametrize(
    ("day", "first", "last"),
    [
        (date(2026, 10, 5), "2026-10-04T23:00Z", "2026-10-05T22:30Z"),  # summer time
        (date(2026, 1, 15), "2026-01-15T00:00Z", "2026-01-15T23:30Z"),  # winter time
        (date(2026, 3, 29), "2026-03-29T00:00Z", "2026-03-29T22:30Z"),  # 46 half hours
        (date(2026, 10, 25), "2026-10-24T23:00Z", "2026-10-25T23:30Z"),  # 50 half hours
    ],
)
def test_request_covers_exactly_the_uk_day(
    source: ElexonMarketIndex, day: date, first: str, last: str
) -> None:
    request = request_for(source, day)
    assert request.url == f"{BASE}/datasets/MID"
    assert request.params == {"from": first, "to": last, "format": "json"}


def test_valid_day_counts_every_row_including_empty_exchange(source: ElexonMarketIndex) -> None:
    assert source.validate(request_for(source), make_payload()) == 96


def test_clock_change_day_with_50_periods_is_valid(source: ElexonMarketIndex) -> None:
    day = date(2026, 10, 25)
    assert source.validate(request_for(source, day), make_payload(day, 50)) == 100


def test_empty_response_is_rejected(source: ElexonMarketIndex) -> None:
    with pytest.raises(ResponseValidationError, match="no records"):
        source.validate(request_for(source), {"data": []})


def test_wrong_date_is_rejected(source: ElexonMarketIndex) -> None:
    payload = make_payload()
    payload["data"][0]["settlementDate"] = "2026-10-04"
    with pytest.raises(ResponseValidationError, match="unexpected dates"):
        source.validate(request_for(source), payload)


def test_period_beyond_the_day_is_rejected(source: ElexonMarketIndex) -> None:
    payload = make_payload()
    payload["data"][0]["settlementPeriod"] = 49
    with pytest.raises(ResponseValidationError, match="invalid settlement periods"):
        source.validate(request_for(source), payload)


def test_unknown_provider_is_rejected(source: ElexonMarketIndex) -> None:
    payload = make_payload()
    payload["data"][0]["dataProvider"] = "NEWEXCHANGE"
    with pytest.raises(ResponseValidationError, match="unknown data providers"):
        source.validate(request_for(source), payload)


def test_duplicate_period_and_provider_is_rejected(source: ElexonMarketIndex) -> None:
    payload = make_payload()
    payload["data"].append(dict(payload["data"][0]))
    with pytest.raises(ResponseValidationError, match="duplicate"):
        source.validate(request_for(source), payload)


def test_missing_price_is_rejected(source: ElexonMarketIndex) -> None:
    payload = make_payload()
    payload["data"][4]["price"] = None
    with pytest.raises(
        ResponseValidationError, match="non numeric price or volume in periods \\[3\\]"
    ):
        source.validate(request_for(source), payload)
