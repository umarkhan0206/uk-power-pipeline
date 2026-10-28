"""Tests for the strategies and the backtest summary."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ukpower.backtest.run import run_backtest, summarise
from ukpower.backtest.strategies import filled_forecast, fixed_rule_schedule, uk_hours
from ukpower.config import BacktestSettings, BatterySettings, FixedRuleSettings

BATTERY = BatterySettings()


def test_uk_hours_follow_the_uk_clock() -> None:
    times = pd.Series(pd.to_datetime(["2026-07-01 16:00", "2026-01-15 17:30"], utc=True))
    assert list(uk_hours(times)) == [17.0, 17.5]  # summer time adds an hour, winter does not


def test_gaps_are_filled_from_the_fallback_and_counted() -> None:
    day = pd.DataFrame({"a": [1.0, np.nan, 3.0], "b": [9.0, 8.0, 7.0]})
    forecast, gaps = filled_forecast(day, "a", "b")
    assert list(forecast) == [1.0, 8.0, 3.0] and gaps == 1


def test_fixed_rule_charges_at_night_and_gives_it_back_in_the_evening() -> None:
    hours = np.arange(48) / 2
    schedule = fixed_rule_schedule(hours, FixedRuleSettings(), BATTERY)
    assert math.isclose(schedule.charge.sum(), 2.0, abs_tol=1e-9)  # 4 half hours at 1 MW
    assert math.isclose(schedule.discharge.sum(), 1.76, abs_tol=1e-9)  # 88% back
    assert schedule.charge[hours == 2.0][0] > 0 and schedule.discharge[hours == 17.0][0] > 0
    assert abs(schedule.stored[-1]) < 1e-9 and schedule.stored.max() <= BATTERY.capacity_mwh + 1e-9


def make_day(date: str, actual: list[float], forecast: list[float]) -> pd.DataFrame:
    start = pd.date_range(f"{date} 00:00", periods=len(actual), freq="30min", tz="UTC")
    return pd.DataFrame(
        {
            "settlement_date": pd.Timestamp(date),
            "start_time_utc": start,
            "month": date[:7],
            "target_price": actual,
            "lgbm_residual": forecast,
            "typical_28d": forecast,
            "same_hh_7d": forecast,
            "lgbm_q25": forecast,
            "lgbm_q75": forecast,
        }
    )


def test_a_wrong_forecast_loses_money_while_perfect_foresight_earns() -> None:
    # Real day: cheap, expensive, cheap. The forecast swaps the expensive and cheap parts,
    # so it charges when the real price is high and discharges when it is low.
    actual = [10.0] * 16 + [100.0] * 16 + [10.0] * 16
    wrong = [100.0] * 16 + [10.0] * 16 + [100.0] * 16
    daily = run_backtest(make_day("2026-01-15", actual, wrong), BacktestSettings())
    assert daily.loc["2026-01-15", "perfect_foresight"] > 0
    assert daily.loc["2026-01-15", "lgbm"] < 0


def test_summary_reports_share_of_perfect() -> None:
    daily = pd.DataFrame(
        {
            "perfect_foresight": [100.0, 300.0],
            "lgbm": [50.0, 150.0],
            "typical_28d": [0.0, 0.0],
            "same_hh_7d": [-10.0, 0.0],
            "lgbm_careful": [0.0, 0.0],
            "fixed_rule": [20.0, 20.0],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
    )
    table = summarise(daily, drop_top_days=1)
    assert table.loc["lgbm", "pct_of_perfect"] == 50.0
    assert table.loc["same_hh_7d", "losing_days"] == 1
    assert table.loc["fixed_rule", "pct_of_perfect_without_top_1_days"] == 20.0
