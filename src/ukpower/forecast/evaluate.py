"""Walk forward evaluation of the baselines and LightGBM.

LightGBM point forecasts run in two variants (see docs/experiments.md):
* direct: forecasts the price itself
* residual: forecasts the distance from the 28 day typical price, then adds it back
Ranges (variant 3) use the residual set up with quantile models at 10, 25, 75 and 90%;
the residual median model is the 50% level.

Run:
    python -m ukpower.forecast.evaluate
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

import pandas as pd

from ukpower.config import PROJECT_ROOT, ForecastSettings, load_settings
from ukpower.forecast.baselines import lag_forecast, profile_forecast, profile_quantiles
from ukpower.forecast.data import TARGET, feature_columns, load_gold
from ukpower.forecast.metrics import score
from ukpower.forecast.models import fit_gbm, predict
from ukpower.forecast.ranges import QUANTILES, qcol, range_summary, sort_quantiles
from ukpower.forecast.splits import Fold, monthly_folds, split
from ukpower.logging_setup import setup_logging

logger = logging.getLogger(__name__)

PROFILE = "typical_28d"
RESIDUAL = "residual_target"  # built from the answer, so it only ever exists inside training
VARIANTS = ["direct", "residual"]
BASELINES = ["same_hh_7d", "same_hh_3d", PROFILE]
METHODS = [*BASELINES, *[f"lgbm_{v}" for v in VARIANTS]]
KEEP = ["settlement_date", "settlement_period", "start_time_utc", TARGET, "target_is_final"]


def add_profile(frame: pd.DataFrame, label_gap_days: int, profile_days: int) -> pd.DataFrame:
    """Add the typical price for every row, each from its own window before its own gap."""
    return frame.assign(**{PROFILE: profile_forecast(frame, frame, label_gap_days, profile_days)})


def run_baselines(
    frame: pd.DataFrame, folds: list[Fold], label_gap_days: int, profile_days: int
) -> pd.DataFrame:
    """One row per test half hour: the actual price, the point baselines and the range baseline."""
    parts = []
    for fold in folds:
        _, test = split(frame, fold)
        out = test[KEEP].copy()
        out["month"] = fold.test_start.strftime("%Y-%m")
        out["same_hh_7d"] = lag_forecast(test, "price_lag7", fallback="price_lag3")
        out["same_hh_3d"] = lag_forecast(test, "price_lag3")
        out[PROFILE] = test[PROFILE]
        levels = profile_quantiles(frame, test, label_gap_days, profile_days, QUANTILES)
        for q in QUANTILES:
            out[qcol("typical", q)] = levels[q]
        parts.append(out)
    return pd.concat(parts)


def run_lightgbm(
    frame: pd.DataFrame,
    folds: list[Fold],
    cfg: ForecastSettings,
    variant: str,
    objective: str = "l1",
    alpha: float | None = None,
) -> tuple[pd.Series, pd.DataFrame, pd.Series]:
    """Monthly retrained LightGBM: forecasts, per fold training details, mean importance."""
    base = [c for c in feature_columns(frame) if c != PROFILE]
    if variant == "direct":
        features, target = base, TARGET
    elif variant == "residual":
        features, target = [*base, PROFILE], RESIDUAL
    else:
        raise ValueError(f"unknown variant {variant!r}")
    if TARGET in features or RESIDUAL in features:
        raise ValueError("the answer must never be a feature")

    label = variant if alpha is None else f"{variant} q{round(alpha * 100):02d}"
    forecasts, details, importances = [], [], []
    for fold in folds:
        train, test = split(frame, fold)
        train = train.assign(**{RESIDUAL: train[TARGET] - train[PROFILE]})
        model = fit_gbm(train, features, cfg.lightgbm, objective, alpha, target=target)
        forecast = predict(model, test)
        if variant == "residual":
            forecast = forecast + test[PROFILE]
        forecasts.append(forecast)
        gain = pd.Series(model.booster.feature_importance("gain"), index=features)
        importances.append(gain / gain.sum())
        details.append({"month": fold.test_start.strftime("%Y-%m"), "trees": model.best_iteration})
        logger.info("LightGBM %s %s: %d trees", label, details[-1]["month"], model.best_iteration)
    importance = pd.concat(importances, axis=1).mean(axis=1).sort_values(ascending=False)
    return pd.concat(forecasts), pd.DataFrame(details).set_index("month"), importance


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

    frame = add_profile(load_gold(settings.gold_path), cfg.label_gap_days, cfg.profile_days)
    first = frame["settlement_date"].min().date()
    last = frame["settlement_date"].max().date()
    folds = monthly_folds(first, last, cfg.test_start, cfg.label_gap_days)
    logger.info("%d folds, %s to %s", len(folds), folds[0].test_start, folds[-1].test_end)

    forecasts = run_baselines(frame, folds, cfg.label_gap_days, cfg.profile_days)
    trees, importance = {}, {}
    for variant in VARIANTS:
        forecast, details, imp = run_lightgbm(frame, folds, cfg, variant)
        forecasts[f"lgbm_{variant}"] = forecast
        trees[variant] = details["trees"]
        importance[variant] = imp

    # Ranges: the residual median model is the 50% level; the others are quantile models
    forecasts[qcol("lgbm", 0.5)] = forecasts["lgbm_residual"]
    for q in QUANTILES:
        if q == 0.5:
            continue
        forecast, details, _ = run_lightgbm(
            frame, folds, cfg, "residual", objective="quantile", alpha=q
        )
        forecasts[qcol("lgbm", q)] = forecast
        trees[f"q{round(q * 100):02d}"] = details["trees"]
    forecasts, crossed_lgbm = sort_quantiles(forecasts, "lgbm")
    forecasts, crossed_typical = sort_quantiles(forecasts, "typical")

    overall, monthly = summarise(forecasts, METHODS)
    range_overall, range_monthly = range_summary(forecasts, ["typical", "lgbm"])

    out = settings.data_path / "results" / "forecasts.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    forecasts.to_parquet(out)

    fair = int(common_rows(forecasts, METHODS).sum())
    print(f"\nTest half hours: {len(forecasts):,} | scored (all methods present): {fair:,}")
    print("\nOverall (£/MWh):\n" + overall.to_string())
    print("\nMAE by month (£/MWh):\n" + monthly.to_string())
    print("\nTrees per month:\n" + pd.DataFrame(trees).to_string())
    print(
        "\nTop 10 features, residual variant (share of gain, averaged over months):\n"
        + (100 * importance["residual"].head(10)).round(1).to_string()
    )
    print(
        f"\nCrossed ranges fixed by sorting: LightGBM {crossed_lgbm:,}, typical {crossed_typical:,}"
    )
    print(
        "\nRanges overall (coverage in %, width and pinball in £/MWh):\n"
        + range_overall.T.to_string()
    )
    print(
        "\nRanges by month (share inside the 10 to 90% band, and its width):\n"
        + range_monthly.to_string()
    )
    logger.info("Saved forecasts to %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
