"""Tests for the evaluation summary."""

from __future__ import annotations

import pandas as pd

from ukpower.forecast.evaluate import common_rows, summarise


def test_every_method_is_scored_on_the_same_half_hours() -> None:
    forecasts = pd.DataFrame(
        {
            "target_price": [10.0, 20.0, 30.0, None],
            "a": [10.0, 20.0, None, 40.0],  # blank in row 3
            "b": [0.0, 0.0, 0.0, 0.0],
            "month": "2025-10",
        }
    )
    fair = common_rows(forecasts, ["a", "b"])
    assert list(fair) == [True, True, False, False]  # row 3: a is blank, row 4: no actual

    overall, monthly = summarise(forecasts, ["a", "b"])
    assert overall.loc["a", "n"] == overall.loc["b", "n"] == 2
    assert overall.loc["a", "mae"] == 0.0
    assert overall.loc["b", "mae"] == 15.0  # errors 10 and 20 on the same two rows
    assert overall.loc["a", "coverage_pct"] == 75.0
    assert monthly.loc["2025-10", "half_hours"] == 2
