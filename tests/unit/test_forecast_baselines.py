"""Tests for the scores and the baselines."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from ukpower.forecast.baselines import lag_forecast, profile_forecast
from ukpower.forecast.metrics import score


def test_score_known_values() -> None:
    result = score(pd.Series([0.0, 10.0]), pd.Series([0.0, 20.0]))
    assert result.n == 2
    assert result.mae == 5.0
    assert math.isclose(result.rmse, math.sqrt(50))


def test_score_leaves_out_missing_values_and_counts_them() -> None:
    result = score(pd.Series([1.0, None, 3.0]), pd.Series([2.0, 5.0, None]))
    assert result.n == 1
    assert result.mae == 1.0


def test_score_refuses_misaligned_or_empty_input() -> None:
    with pytest.raises(ValueError, match="same index"):
        score(pd.Series([1.0], index=[0]), pd.Series([1.0], index=[1]))
    with pytest.raises(ValueError, match="nothing to score"):
        score(pd.Series([None], dtype=float), pd.Series([1.0]))


def test_lag_forecast_falls_back_only_when_the_first_lag_is_blank() -> None:
    test = pd.DataFrame({"price_lag7": [10.0, None], "price_lag3": [99.0, 30.0]})
    assert list(lag_forecast(test, "price_lag7", "price_lag3")) == [10.0, 30.0]


def daily_history(first: str, last: str, weekend: int = 0) -> pd.DataFrame:
    """One half hour per day at 17:30, priced at the day's number, so windows are visible."""
    days = pd.date_range(first, last, freq="D")
    return pd.DataFrame(
        {
            "settlement_date": days,
            "uk_hour": 17.5,
            "is_weekend": weekend,
            "target_price": [float(d.day) for d in days],
        }
    )


def test_profile_uses_only_the_window_before_the_gap() -> None:
    history = daily_history("2025-10-01", "2025-10-20")
    # Prices the forecast must never see: the decision day, the gap days and the day itself
    future = history["settlement_date"] >= pd.Timestamp("2025-10-17")
    history.loc[future, "target_price"] = 1_000_000.0

    test = pd.DataFrame(
        {"settlement_date": [pd.Timestamp("2025-10-20")], "uk_hour": [17.5], "is_weekend": [0]}
    )

    forecast = profile_forecast(history, test, label_gap_days=3, profile_days=5)
    # gap 3 -> newest = 20 - 1 - 3 = 16th, window 12th to 16th, median 14
    assert forecast.iloc[0] == 14.0


def test_profile_keeps_weekdays_and_weekends_apart() -> None:
    weekdays = daily_history("2025-10-01", "2025-10-10", weekend=0)
    weekends = daily_history("2025-10-01", "2025-10-10", weekend=1)
    weekends["target_price"] += 100.0
    history = pd.concat([weekdays, weekends], ignore_index=True)

    test = pd.DataFrame(
        {
            "settlement_date": [pd.Timestamp("2025-10-13")] * 2,
            "uk_hour": [17.5, 17.5],
            "is_weekend": [0, 1],
        }
    )
    forecast = profile_forecast(history, test, label_gap_days=2, profile_days=3)
    # newest = 13 - 1 - 2 = 10th, window 8th to 10th, median 9 (and 109 at weekends)
    assert list(forecast) == [9.0, 109.0]


def test_profile_is_blank_when_the_window_has_no_matching_slot() -> None:
    history = daily_history("2025-10-01", "2025-10-10")
    test = pd.DataFrame(
        {"settlement_date": [pd.Timestamp("2025-10-13")], "uk_hour": [3.0], "is_weekend": [0]}
    )
    assert profile_forecast(history, test, label_gap_days=2, profile_days=3).isna().all()
