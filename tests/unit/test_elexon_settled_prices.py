"""Tests for the settled (best view) system prices source."""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from ukpower.ingestion.sources.base import ResponseValidationError, SourceRequest
from ukpower.ingestion.sources.elexon_settled_prices import KNOWN_RUN_TYPES, ElexonSettledPrices
from ukpower.ingestion.sources.elexon_system_prices import ElexonSystemPrices

TZ = ZoneInfo("Europe/London")
BASE = "https://example.test/api"
DAY = date(2026, 10, 20)
SUNDAY, MONDAY = date(2026, 10, 25), date(2026, 10, 26)


@pytest.fixture
def source() -> ElexonSettledPrices:
    return ElexonSettledPrices(BASE, TZ, long_refetch_days=430, long_refetch_weekday=6)


def make_payload(
    day: date = DAY, periods: int = 48, run_type: str = "II", price: float = 50.0
) -> dict[str, Any]:
    return {
        "data": [
            {
                "settlementDate": day.isoformat(),
                "settlementPeriod": p,
                "runType": run_type,
                "systemBuyPrice": price,
                "systemSellPrice": price,
            }
            for p in range(1, periods + 1)
        ]
    }


def request_for(source: ElexonSettledPrices, day: date = DAY) -> SourceRequest:
    (request,) = source.requests_for_day(day)
    return request


def test_request_asks_for_one_day_and_no_run_type(source: ElexonSettledPrices) -> None:
    request = request_for(source)
    assert request.partition == "2026-10-20"
    assert request.url == f"{BASE}/market-reports/best-view-prices"
    assert request.params == {"startDate": "2026-10-20", "endDate": "2026-10-20", "format": "json"}


def test_valid_day_returns_record_count(source: ElexonSettledPrices) -> None:
    assert source.validate(request_for(source), make_payload()) == 48


def test_clock_change_day_with_46_periods_is_valid(source: ElexonSettledPrices) -> None:
    day = date(2026, 3, 29)
    assert source.validate(request_for(source, day), make_payload(day=day, periods=46)) == 46


@pytest.mark.parametrize("run_type", sorted(KNOWN_RUN_TYPES))
def test_every_known_run_type_is_accepted(source: ElexonSettledPrices, run_type: str) -> None:
    assert source.validate(request_for(source), make_payload(run_type=run_type)) == 48


def test_unknown_run_type_is_rejected(source: ElexonSettledPrices) -> None:
    with pytest.raises(ResponseValidationError, match="unknown run types"):
        source.validate(request_for(source), make_payload(run_type="XX"))


def test_missing_price_is_rejected(source: ElexonSettledPrices) -> None:
    payload = make_payload()
    payload["data"][10]["systemSellPrice"] = None
    with pytest.raises(ResponseValidationError, match="non numeric prices in periods \\[11\\]"):
        source.validate(request_for(source), payload)


def test_empty_response_is_rejected(source: ElexonSettledPrices) -> None:
    with pytest.raises(ResponseValidationError, match="no records"):
        source.validate(request_for(source), {"data": []})


def test_duplicate_period_is_rejected(source: ElexonSettledPrices) -> None:
    payload = make_payload()
    payload["data"].append(dict(payload["data"][0]))
    with pytest.raises(ResponseValidationError, match="duplicate settlement periods"):
        source.validate(request_for(source), payload)


def test_wrong_date_is_rejected(source: ElexonSettledPrices) -> None:
    payload = make_payload()
    payload["data"][0]["settlementDate"] = "2026-10-19"
    with pytest.raises(ResponseValidationError, match="unexpected dates"):
        source.validate(request_for(source), payload)


def test_long_look_back_only_on_the_chosen_weekday(source: ElexonSettledPrices) -> None:
    assert source.refetch_days(3, SUNDAY) == 430
    assert source.refetch_days(3, MONDAY) == 3


def test_long_look_back_never_shortens_the_default() -> None:
    off = ElexonSettledPrices(BASE, TZ, long_refetch_days=0, long_refetch_weekday=6)
    assert off.refetch_days(3, SUNDAY) == 3


def test_other_sources_keep_the_shared_window() -> None:
    assert ElexonSystemPrices(BASE, TZ).refetch_days(3, SUNDAY) == 3
