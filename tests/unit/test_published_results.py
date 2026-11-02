"""The published results must agree with the backtest, so the dashboard never contradicts it."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ukpower.config import load_settings

RESULTS = Path(__file__).resolve().parents[2] / "results"
pytestmark = pytest.mark.skipif(
    not (RESULTS / "forecasts.parquet").exists(), reason="results not published"
)


def day_profit(day: pd.DataFrame, plan: str, wear: float) -> float:
    """Price x energy (positive plan sells), minus wear on what was sold."""
    sold = day[plan].clip(lower=0)
    return float((day[plan] * day["target_price"]).sum() - wear * sold.sum())


@pytest.mark.parametrize(
    ("plan", "strategy"), [("plan_lgbm", "lgbm"), ("plan_perfect", "perfect_foresight")]
)
def test_published_plans_reproduce_the_backtest_to_the_penny(plan: str, strategy: str) -> None:
    forecasts = pd.read_parquet(RESULTS / "forecasts.parquet")
    daily = pd.read_parquet(RESULTS / "backtest_daily.parquet")
    wear = load_settings().backtest.battery.wear_cost_per_mwh

    per_day = forecasts.groupby("settlement_date").apply(
        lambda g, p=plan: day_profit(g, p, wear), include_groups=False
    )
    per_day.index = pd.to_datetime(per_day.index)
    expected = daily[strategy]
    expected.index = pd.to_datetime(expected.index)
    assert np.allclose(per_day.reindex(expected.index).to_numpy(), expected.to_numpy(), atol=0.01)
