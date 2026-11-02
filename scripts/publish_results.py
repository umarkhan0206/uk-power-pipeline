"""Copy a small public slice of the results into results/ for the dashboard.

results/ is committed to git, so the dashboard can run on Streamlit Community Cloud
without the pipeline, the data folder or Databricks.

Run (after python -m ukpower.forecast.evaluate and python -m ukpower.backtest.run):
    python scripts/publish_results.py
"""

from __future__ import annotations

import sys

import pandas as pd

from ukpower.backtest.battery import plan_day
from ukpower.config import PROJECT_ROOT, BatterySettings, load_settings

FORECAST_COLUMNS = [
    "settlement_date",
    "settlement_period",
    "start_time_utc",
    "month",
    "target_price",
    "typical_28d",
    "lgbm_residual",
    "lgbm_q10",
    "lgbm_q25",
    "lgbm_q50",
    "lgbm_q75",
    "lgbm_q90",
]
#: plan column -> forecast it plans with
PLANS = {"plan_lgbm": "lgbm_residual", "plan_perfect": "target_price"}


def add_plans(forecasts: pd.DataFrame, battery: BatterySettings) -> pd.DataFrame:
    """Battery plan per half hour in MWh: positive sells (discharges), negative buys (charges)."""
    out = forecasts.copy()
    for column in PLANS:
        out[column] = float("nan")
    for _, day in out.sort_values("start_time_utc").groupby("settlement_date"):
        if day["target_price"].isna().any():
            continue
        for column, source in PLANS.items():
            prices = day[source].fillna(day["typical_28d"]).to_numpy(dtype=float)
            schedule = plan_day(prices, battery)
            out.loc[day.index, column] = schedule.discharge - schedule.charge
    return out


def main() -> int:
    settings = load_settings()
    source = settings.data_path / "results"
    target = PROJECT_ROOT / "results"
    target.mkdir(exist_ok=True)

    forecasts = pd.read_parquet(source / "forecasts.parquet")[FORECAST_COLUMNS]
    forecasts = add_plans(forecasts, settings.backtest.battery)
    daily = pd.read_parquet(source / "backtest_daily.parquet")

    forecasts.to_parquet(target / "forecasts.parquet", index=False)
    daily.to_parquet(target / "backtest_daily.parquet")
    for name in ("forecasts.parquet", "backtest_daily.parquet"):
        size = (target / name).stat().st_size / 1024
        print(f"Saved results/{name}: {size:,.0f} KB")
    print(f"{len(forecasts):,} half hours, {forecasts['plan_lgbm'].notna().sum():,} with a plan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
