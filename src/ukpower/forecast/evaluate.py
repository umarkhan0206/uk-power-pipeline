"""Walk forward evaluation of the baselines.

Run:
    python -m ukpower.forecast.evaluate
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

import pandas as pd

from ukpower.config import PROJECT_ROOT, load_settings
from ukpower.forecast.baselines import lag_forecast, profile_forecast
from ukpower.forecast.data import TARGET, load_gold
from ukpower.forecast.metrics import score
from ukpower.forecast.splits import Fold, monthly_folds, split
from ukpower.logging_setup import setup_logging

logger = logging.getLogger(__name__)

BASELINES = ["same_hh_7d", "same_hh_3d", "typical_28d"]
KEEP = ["settlement_date", "settlement_period", "start_time_utc", TARGET, "target_is_final"]


def run_baselines(
    frame: pd.DataFrame, folds: list[Fold], label_gap_days: int, profile_days: int
) -> pd.DataFrame:
    """One row per test half hour: the actual price and every baseline's forecast."""
    parts = []
    for fold in folds:
        _, test = split(frame, fold)
        out = test[KEEP].copy()
        out["month"] = fold.test_start.strftime("%Y-%m")
        out["same_hh_7d"] = lag_forecast(test, "price_lag7", fallback="price_lag3")
        out["same_hh_3d"] = lag_forecast(test, "price_lag3")
        # profile_forecast picks its own window before each day's gap, so the full frame is safe
        out["typical_28d"] = profile_forecast(frame, test, label_gap_days, profile_days)
        parts.append(out)
    return pd.concat(parts)


def common_rows(forecasts: pd.DataFrame, methods: list[str]) -> pd.Series:
    """Half hours where the actual and every method's forecast exist, so the scores are fair."""
    return forecasts[TARGET].notna() & forecasts[methods].notna().all(axis=1)


def summarise(forecasts: pd.DataFrame, methods: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Overall and per month scores, all on the same half hours."""
    fair = forecasts[common_rows(forecasts, methods)]
    overall = pd.DataFrame(
        [
            {
                "method": m,
                "coverage_pct": round(100 * forecasts[m].notna().mean(), 1),
                **vars(score(fair[TARGET], fair[m])),
            }
            for m in methods
        ]
    ).set_index("method")
    monthly = pd.DataFrame(
        {m: fair.groupby("month").apply(lambda g, m=m: score(g[TARGET], g[m]).mae) for m in methods}
    )
    monthly.insert(0, "half_hours", fair.groupby("month").size())
    return overall.round(2), monthly.round(2)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ukpower-evaluate", description=__doc__)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(PROJECT_ROOT / "logs", args.log_level)
    cfg = settings.forecast

    frame = load_gold(settings.gold_path)
    first = frame["settlement_date"].min().date()
    last = frame["settlement_date"].max().date()
    folds = monthly_folds(first, last, cfg.test_start, cfg.label_gap_days)
    logger.info("%d folds, %s to %s", len(folds), folds[0].test_start, folds[-1].test_end)

    forecasts = run_baselines(frame, folds, cfg.label_gap_days, cfg.profile_days)
    overall, monthly = summarise(forecasts, BASELINES)

    out = settings.data_path / "results" / "baseline_forecasts.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    forecasts.to_parquet(out)

    fair = int(common_rows(forecasts, BASELINES).sum())
    print(f"\nTest half hours: {len(forecasts):,} | scored (all methods present): {fair:,}")
    print("\nOverall (£/MWh):\n" + overall.to_string())
    print("\nMAE by month (£/MWh):\n" + monthly.to_string())
    logger.info("Saved forecasts to %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
