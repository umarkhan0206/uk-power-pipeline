"""Run every strategy over the test year and compare the money made.

Run:
    python -m ukpower.backtest.run
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

import pandas as pd

from ukpower.backtest.battery import Schedule, plan_day, profit
from ukpower.backtest.strategies import filled_forecast, fixed_rule_schedule, uk_hours
from ukpower.config import PROJECT_ROOT, BacktestSettings, load_settings
from ukpower.logging_setup import setup_logging

logger = logging.getLogger(__name__)

TARGET = "target_price"
FALLBACK = "typical_28d"
#: Strategy name -> forecast column it plans with
FORECASTS = {
    "perfect_foresight": TARGET,
    "lgbm": "lgbm_residual",
    "typical_28d": "typical_28d",
    "same_hh_7d": "same_hh_7d",
}
#: Careful strategies: name -> (column planned as the buy price, column planned as the sell price)
CAREFUL = {"lgbm_careful": ("lgbm_q75", "lgbm_q25")}
STRATEGIES = [*FORECASTS, *CAREFUL, "fixed_rule"]


def run_backtest(forecasts: pd.DataFrame, cfg: BacktestSettings) -> pd.DataFrame:
    """One row per day: the money each strategy made at the settled price, and MWh sold."""
    battery = cfg.battery
    rows = []
    for day, group in forecasts.sort_values("start_time_utc").groupby("settlement_date"):
        actual = group[TARGET].to_numpy(dtype=float)
        if pd.isna(actual).any():
            logger.warning("Skipping %s: settled price missing", day.date())
            continue
        row: dict[str, object] = {"settlement_date": day, "month": group["month"].iloc[0]}

        def record(name: str, schedule: Schedule, gaps: int, row: dict = row) -> None:
            row[name] = profit(schedule, actual, battery)
            row[f"{name}_mwh"] = float(schedule.discharge.sum())
            row[f"{name}_gaps"] = gaps

        for name, column in FORECASTS.items():
            forecast, gaps = filled_forecast(group, column, FALLBACK)
            record(name, plan_day(forecast, battery), gaps)
        for name, (buy_col, sell_col) in CAREFUL.items():
            buy, buy_gaps = filled_forecast(group, buy_col, FALLBACK)
            sell, sell_gaps = filled_forecast(group, sell_col, FALLBACK)
            record(name, plan_day(buy, battery, sell_prices=sell), buy_gaps + sell_gaps)
        fixed = fixed_rule_schedule(uk_hours(group["start_time_utc"]), cfg.fixed_rule, battery)
        record("fixed_rule", fixed, 0)
        rows.append(row)
    return pd.DataFrame(rows).set_index("settlement_date")


def summarise(daily: pd.DataFrame, drop_top_days: int = 5) -> pd.DataFrame:
    """Totals, share of perfect foresight, losing days, MWh sold, and the same without the best days."""
    best_days = daily["perfect_foresight"].nlargest(drop_top_days).index
    calm = daily.drop(best_days)
    perfect, perfect_calm = daily["perfect_foresight"].sum(), calm["perfect_foresight"].sum()
    names = [n for n in STRATEGIES if n in daily.columns]
    return pd.DataFrame(
        {
            name: {
                "total_gbp": daily[name].sum(),
                "pct_of_perfect": 100 * daily[name].sum() / perfect,
                "losing_days": int((daily[name] < -0.005).sum()),
                "worst_day_gbp": daily[name].min(),
                "mwh_sold": daily[f"{name}_mwh"].sum() if f"{name}_mwh" in daily else float("nan"),
                f"pct_of_perfect_without_top_{drop_top_days}_days": 100
                * calm[name].sum()
                / perfect_calm,
            }
            for name in names
        }
    ).T.round(1)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ukpower-backtest", description=__doc__)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(PROJECT_ROOT / "logs", args.log_level)
    path = settings.data_path / "results" / "forecasts.parquet"
    forecasts = pd.read_parquet(path)
    forecasts["start_time_utc"] = pd.to_datetime(forecasts["start_time_utc"], utc=True)
    logger.info("Loaded %d forecast half hours from %s", len(forecasts), path)

    daily = run_backtest(forecasts, settings.backtest)
    out = settings.data_path / "results" / "backtest_daily.parquet"
    daily.to_parquet(out)

    gaps = {c.removesuffix("_gaps"): int(daily[c].sum()) for c in daily if c.endswith("_gaps")}
    print(f"\nDays: {len(daily)} ({daily.index.min().date()} to {daily.index.max().date()})")
    print(f"Forecast gaps filled with {FALLBACK}: {gaps}")
    print("\nOverall (£, 1 MW / 2 MWh battery):\n" + summarise(daily).to_string())
    print(
        "\nProfit by month (£):\n" + daily.groupby("month")[STRATEGIES].sum().round(0).to_string()
    )
    print(
        "\nBest 5 days for perfect foresight (£):\n"
        + daily.nlargest(5, "perfect_foresight")[STRATEGIES].round(0).to_string()
    )
    logger.info("Saved daily results to %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
