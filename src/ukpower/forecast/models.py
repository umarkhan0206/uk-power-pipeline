"""LightGBM forecasts, trained only on what each fold allows.

For each fold, the last validation_days target days of the training data are held back
to choose the number of trees (early stopping). The model is then retrained on all of the
training data with that number, so the newest allowed days are used too. The test month
is never used to choose anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from ukpower.config import LightGbmSettings
from ukpower.forecast.data import TARGET

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FittedModel:
    """A trained booster, the features it expects, the number of trees chosen, and the
    practice run's errors on the held back days (actual minus forecast), used to calibrate."""

    booster: lgb.Booster
    features: list[str]
    best_iteration: int
    holdout_errors: np.ndarray


def lgb_params(cfg: LightGbmSettings, objective: str, alpha: float | None = None) -> dict[str, Any]:
    """LightGBM parameters. Deterministic, so the same data gives the same model."""
    params: dict[str, Any] = {
        "objective": objective,
        "metric": objective,
        "learning_rate": cfg.learning_rate,
        "num_leaves": cfg.num_leaves,
        "min_child_samples": cfg.min_child_samples,
        "feature_fraction": cfg.feature_fraction,
        "bagging_fraction": cfg.bagging_fraction,
        "bagging_freq": 1,
        "lambda_l2": cfg.lambda_l2,
        "seed": cfg.seed,
        "deterministic": True,
        "force_row_wise": True,
        "verbose": -1,
    }
    if alpha is not None:
        params["alpha"] = alpha
    return params


def holdout_split(train: pd.DataFrame, validation_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split training rows into (fit, validation): validation is the last N target days.

    Raises:
        ValueError: if there are not at least twice as many days as validation_days.
    """
    days = sorted(train["settlement_date"].unique())
    if len(days) < 2 * validation_days:
        raise ValueError(
            f"only {len(days)} training days; need at least {2 * validation_days} "
            f"for a {validation_days} day validation window"
        )
    first_valid = days[-validation_days]
    is_valid = train["settlement_date"] >= first_valid
    return train[~is_valid], train[is_valid]


def fit_gbm(
    train: pd.DataFrame,
    features: list[str],
    cfg: LightGbmSettings,
    objective: str = "l1",
    alpha: float | None = None,
    target: str = TARGET,
) -> FittedModel:
    """Choose the number of trees on a held back window, then retrain on all training rows.

    "l1" aims at the median, which suits scoring by average absolute error.
    `target` is the column to learn, for example the price or its distance from a baseline.
    The practice model (trained without the held back days) also records its errors there,
    so ranges can be calibrated without touching the test month.
    """
    if target in features:
        raise ValueError(f"the target {target!r} cannot also be a feature")
    train = train[train[target].notna()]
    fit, valid = holdout_split(train, cfg.validation_days)
    params = lgb_params(cfg, objective, alpha)

    probe = lgb.train(
        params,
        lgb.Dataset(fit[features], fit[target]),
        num_boost_round=cfg.max_rounds,
        valid_sets=[lgb.Dataset(valid[features], valid[target])],
        callbacks=[lgb.early_stopping(cfg.early_stopping_rounds, verbose=False)],
    )
    best = probe.best_iteration or cfg.max_rounds
    holdout_errors = valid[target].to_numpy(dtype=float) - probe.predict(
        valid[features], num_iteration=best
    )

    final = lgb.train(params, lgb.Dataset(train[features], train[target]), num_boost_round=best)
    return FittedModel(
        booster=final, features=features, best_iteration=best, holdout_errors=holdout_errors
    )


def predict(model: FittedModel, frame: pd.DataFrame) -> pd.Series:
    """Forecast for every row, aligned to the frame's index."""
    return pd.Series(model.booster.predict(frame[model.features]), index=frame.index)
