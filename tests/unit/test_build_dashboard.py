"""Tests for the dashboard build: the page's numbers come straight from the results."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from ukpower.config import BatterySettings

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_dashboard.py"
spec = importlib.util.spec_from_file_location("build_dashboard", SCRIPT)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def make_results() -> tuple[pd.DataFrame, pd.DataFrame]:
    days = pd.to_datetime(["2026-01-15", "2026-01-16"])
    rows = []
    for day in days:
        start = pd.Timestamp(day, tz="UTC")
        for p in range(48):
            rows.append(
                {
                    "settlement_date": day,
                    "settlement_period": p + 1,
                    "start_time_utc": start + pd.Timedelta(minutes=30 * p),
                    "target_price": 100.0,
                    "lgbm_residual": 90.0,
                    "typical_28d": 80.0,
                    "lgbm_q10": 60.0,
                    "lgbm_q90": 130.0,
                    "plan_lgbm": 0.0,
                    "plan_perfect": 0.0,
                }
            )
    daily = pd.DataFrame(
        {
            "perfect_foresight": [100.0, 300.0],
            "lgbm": [50.0, 100.0],
            "typical_28d": [10.0, 20.0],
            "fixed_rule": [5.0, 5.0],
            "same_hh_7d": [-1.0, 1.0],
        },
        index=days,
    )
    return pd.DataFrame(rows), daily


def test_totals_and_errors_come_from_the_results() -> None:
    forecasts, daily = make_results()
    data = build.build_data(forecasts, daily, BatterySettings())
    assert data["totals"]["lgbm"] == 150.0
    assert data["totals"]["perfect_foresight"] == 400.0
    assert data["mae"] == {"lgbm": 10.0, "typical": 20.0, "n": 96}
    assert data["start"] == "2026-01-15" and data["end"] == "2026-01-16"


def test_each_day_has_48_half_hours_in_uk_time() -> None:
    forecasts, daily = make_results()
    day = build.build_data(forecasts, daily, BatterySettings())["days"]["2026-01-15"]
    assert len(day["t"]) == 48 and day["t"][0] == "00:00" and day["t"][-1] == "23:30"
    assert day["lo"][0] == 60.0 and day["hi"][0] == 130.0


def test_render_embeds_the_data_safely() -> None:
    template = "<script>const D = /*__DATA__*/null;</script>"
    page = build.render({"note": "</script><b>"}, template)
    assert "</script><b>" not in page.split("const D = ", 1)[1].split(";</script>")[0]
    embedded = page.split("const D = ", 1)[1].rsplit(";</script>", 1)[0]
    assert json.loads(embedded.replace("<\\/", "</")) == {"note": "</script><b>"}


def test_render_refuses_a_template_without_the_placeholder() -> None:
    with pytest.raises(ValueError, match="placeholder"):
        build.render({}, "<html></html>")


def test_battery_settings_come_from_config_not_the_page() -> None:
    forecasts, daily = make_results()
    small = BatterySettings(power_mw=0.5, capacity_mwh=1.0, round_trip_efficiency=0.9)
    battery = build.build_data(forecasts, daily, small)["battery"]
    assert battery["power_mw"] == 0.5 and battery["capacity_mwh"] == 1.0
    assert battery["round_trip_efficiency"] == 0.9 and battery["wear_cost_per_mwh"] == 10.0
