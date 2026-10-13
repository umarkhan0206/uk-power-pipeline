"""Tests for deciding which days to fetch."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from ukpower.config import load_settings
from ukpower.ingestion.landing import LandingZone
from ukpower.ingestion.planner import plan_days
from ukpower.ingestion.sources import build_sources
from ukpower.ingestion.sources.base import latest_forecast_day

UK = ZoneInfo("Europe/London")
START = date(2025, 1, 1)
LATEST = date(2025, 1, 10)


@dataclass
class StubSource:
    """Only the attribute plan_days reads."""

    refetch_recent: bool = True


def plan(landed: set[str], refetch_days: int = 3, refetch_recent: bool = True) -> list[date]:
    source: Any = StubSource(refetch_recent)
    return plan_days(source, landed, START, LATEST, refetch_days)


# Which days get planned


def test_nothing_landed_plans_every_day() -> None:
    days = plan(set(), refetch_days=0)
    assert days[0] == START
    assert days[-1] == LATEST
    assert len(days) == 10


def test_landed_days_are_skipped() -> None:
    days = plan({"2025-01-02", "2025-01-03"}, refetch_days=0)
    assert date(2025, 1, 2) not in days
    assert date(2025, 1, 3) not in days
    assert len(days) == 8


def test_recent_days_are_fetched_again_even_if_landed() -> None:
    everything = {f"2025-01-{d:02d}" for d in range(1, 11)}
    assert plan(everything, refetch_days=3) == [date(2025, 1, 8), date(2025, 1, 9), LATEST]


def test_forecast_sources_are_not_fetched_again() -> None:
    everything = {f"2025-01-{d:02d}" for d in range(1, 11)}
    assert plan(everything, refetch_days=3, refetch_recent=False) == []


def test_refetch_never_goes_before_start() -> None:
    source: Any = StubSource()
    assert plan_days(source, set(), START, START, refetch_days=3) == [START]


def test_latest_before_start_plans_nothing() -> None:
    source: Any = StubSource()
    assert plan_days(source, set(), START, date(2024, 12, 31), refetch_days=3) == []


# When tomorrow's forecasts become available


def test_forecast_day_is_today_before_decision_time() -> None:
    before = datetime(2026, 10, 13, 8, 59, tzinfo=UTC)  # 09:59 UK, before 10:00
    assert latest_forecast_day(before, time(10, 0), UK) == date(2026, 10, 13)


def test_forecast_day_is_tomorrow_after_decision_time() -> None:
    after = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)  # 10:00 UK exactly
    assert latest_forecast_day(after, time(10, 0), UK) == date(2026, 10, 14)


def test_forecast_day_in_winter_uses_gmt() -> None:
    # 09:30 UTC in December is 09:30 UK time: still before the 10:00 decision.
    assert latest_forecast_day(datetime(2026, 12, 14, 9, 30, tzinfo=UTC), time(10, 0), UK) == date(
        2026, 12, 14
    )


# Every real source uses the right rule (this would have caught the missing override)


def test_every_source_has_the_right_latest_day() -> None:
    settings = load_settings()
    noon = datetime(2026, 10, 13, 11, 0, tzinfo=UTC)  # 12:00 UK, after the decision
    forecasts = {"elexon_demand_forecast", "elexon_wind_forecast", "open_meteo_ecmwf"}

    for source in build_sources(settings):
        latest = source.latest_available_day(noon, settings.tz)
        if source.name in forecasts:
            assert latest == date(2026, 10, 14), source.name
            assert source.refetch_recent is False, source.name
        else:
            assert latest == date(2026, 10, 12), source.name
            assert source.refetch_recent is True, source.name


# What counts as "already downloaded"


def test_landed_partitions_need_a_data_file(tmp_path: Path) -> None:
    zone = LandingZone(tmp_path)
    (tmp_path / "src" / "2025-01-01").mkdir(parents=True)
    (tmp_path / "src" / "2025-01-01" / "x.json").write_text("{}")
    (tmp_path / "src" / "2025-01-02").mkdir()  # empty folder
    (tmp_path / "src" / "2025-01-03").mkdir()
    (tmp_path / "src" / "2025-01-03" / "x.meta.json").write_text("{}")  # metadata only

    assert zone.landed_partitions("src") == {"2025-01-01"}


def test_landed_partitions_for_unknown_source_is_empty(tmp_path: Path) -> None:
    assert LandingZone(tmp_path).landed_partitions("nothing") == set()


@pytest.mark.parametrize("bad", ["../x", "a/b"])
def test_landed_partitions_rejects_unsafe_names(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        LandingZone(tmp_path).landed_partitions(bad)
