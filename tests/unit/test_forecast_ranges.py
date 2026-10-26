"""Tests for range forecasts: pinball loss, ordering, scores and the range baseline."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ukpower.forecast.baselines import profile_forecast, profile_quantiles
from ukpower.forecast.metrics import pinball
from ukpower.forecast.ranges import QUANTILES, qcol, range_scores, sort_quantiles


def test_pinball_punishes_the_wrong_side_more() -> None:
    # 90% level: too low by 2 costs 0.9 x 2, too high by 2 costs 0.1 x 2
    assert math.isclose(pinball([10.0], [8.0], 0.9), 1.8)
    assert math.isclose(pinball([10.0], [12.0], 0.9), 0.2)
    assert math.isclose(pinball([10.0, np.nan], [8.0, 1.0], 0.9), 1.8)


def test_crossed_levels_are_sorted_and_counted() -> None:
    frame = pd.DataFrame(
        [[1.0, 2.0, 3.0, 4.0, 5.0], [3.0, 2.0, 1.0, 4.0, 5.0], [np.nan, 2.0, 1.0, 4.0, 5.0]],
        columns=[qcol("m", q) for q in QUANTILES],
    )
    out, crossed = sort_quantiles(frame, "m")
    assert crossed == 1
    assert list(out.iloc[1]) == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert np.isnan(out.iloc[2, 0])  # incomplete rows are left alone
    assert list(frame.iloc[1]) == [3.0, 2.0, 1.0, 4.0, 5.0]  # input unchanged


def test_range_scores_count_coverage_and_width() -> None:
    frame = pd.DataFrame({"target_price": [5.0, 15.0, 25.0, 35.0]})
    for q, value in zip(QUANTILES, [10.0, 12.0, 20.0, 28.0, 30.0], strict=True):
        frame[qcol("m", q)] = value
    s = range_scores(frame, "m")
    assert s["below_q10_pct"] == 25.0  # only 5 is below 10
    assert s["inside_10_90_pct"] == 50.0  # 15 and 25
    assert s["inside_25_75_pct"] == 50.0  # 15 and 25 are between 12 and 28
    assert s["width_10_90"] == 20.0


def test_range_baseline_uses_the_same_window_as_the_typical_price() -> None:
    days = pd.date_range("2025-10-01", "2025-10-20", freq="D")
    history = pd.DataFrame(
        {
            "settlement_date": days,
            "uk_hour": 17.5,
            "is_weekend": 0,
            "target_price": [float(d.day) for d in days],
        }
    )
    history.loc[history["settlement_date"] >= pd.Timestamp("2025-10-17"), "target_price"] = 1e6
    test = pd.DataFrame(
        {"settlement_date": [pd.Timestamp("2025-10-20")], "uk_hour": [17.5], "is_weekend": [0]}
    )
    levels = profile_quantiles(history, test, 3, 5, QUANTILES)
    # window 12th to 16th: values 12..16
    assert list(levels.iloc[0].round(2)) == [12.4, 13.0, 14.0, 15.0, 15.6]
    assert levels.iloc[0][0.5] == profile_forecast(history, test, 3, 5).iloc[0]
