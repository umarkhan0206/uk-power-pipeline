"""Tests for the LightGBM wrapper."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ukpower.config import LightGbmSettings
from ukpower.forecast.models import fit_gbm, holdout_split, predict

CFG = LightGbmSettings(
    max_rounds=300, early_stopping_rounds=20, validation_days=10, min_child_samples=5
)


def make_train(days: int = 60, per_day: int = 8, seed: int = 0) -> pd.DataFrame:
    """Price = 2 x wind feature + noise, so a model should learn the slope."""
    rng = np.random.default_rng(seed)
    dates = np.repeat(pd.date_range("2025-01-01", periods=days, freq="D"), per_day)
    x = rng.uniform(0, 100, size=len(dates))
    return pd.DataFrame(
        {"settlement_date": dates, "x": x, "target_price": 2 * x + rng.normal(0, 1, len(dates))}
    )


def test_holdout_is_the_last_days_and_does_not_overlap() -> None:
    train = make_train(days=30)
    fit, valid = holdout_split(train, validation_days=10)
    assert valid["settlement_date"].nunique() == 10
    assert valid["settlement_date"].min() == pd.Timestamp("2025-01-21")
    assert fit["settlement_date"].max() < valid["settlement_date"].min()


def test_holdout_refuses_too_few_days() -> None:
    with pytest.raises(ValueError, match="need at least 20"):
        holdout_split(make_train(days=15), validation_days=10)


def test_model_learns_a_simple_relationship() -> None:
    model = fit_gbm(make_train(), ["x"], CFG)
    test = pd.DataFrame({"x": [10.0, 50.0, 90.0]}, index=[7, 8, 9])
    forecast = predict(model, test)
    assert list(forecast.index) == [7, 8, 9]
    assert np.allclose(forecast, [20.0, 100.0, 180.0], atol=8.0)


def test_rows_without_a_target_are_ignored() -> None:
    train = make_train()
    train.loc[train.index[:50], "target_price"] = np.nan
    model = fit_gbm(train, ["x"], CFG)
    assert model.best_iteration > 0


def test_same_data_gives_the_same_model() -> None:
    test = pd.DataFrame({"x": [10.0, 50.0, 90.0]})
    first = predict(fit_gbm(make_train(), ["x"], CFG), test)
    second = predict(fit_gbm(make_train(), ["x"], CFG), test)
    assert first.equals(second)


def test_any_target_column_can_be_learned() -> None:
    train = make_train().rename(columns={"target_price": "distance"})
    model = fit_gbm(train, ["x"], CFG, target="distance")
    assert np.allclose(predict(model, pd.DataFrame({"x": [50.0]})), [100.0], atol=8.0)


def test_the_target_cannot_also_be_a_feature() -> None:
    with pytest.raises(ValueError, match="cannot also be a feature"):
        fit_gbm(make_train(), ["x", "target_price"], CFG)


def test_holdout_errors_come_from_the_held_back_days() -> None:
    train = make_train()
    model = fit_gbm(train, ["x"], CFG)
    held_back = train["settlement_date"] >= sorted(train["settlement_date"].unique())[-10]
    assert len(model.holdout_errors) == int(held_back.sum())
    assert abs(float(np.median(model.holdout_errors))) < 5.0  # a good model is roughly centred
