"""Simple forecasts that need no machine learning. A model must beat these to be worth using.

Every baseline uses only what was known at the decision: the lag features were masked by
publish time in Gold, and the typical price profile obeys the same label gap as training.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ukpower.forecast.data import TARGET

PROFILE_KEYS = ["uk_hour", "is_weekend"]


def lag_forecast(test: pd.DataFrame, primary: str, fallback: str | None = None) -> pd.Series:
    """The same half hour some days earlier, optionally falling back to another lag."""
    forecast = test[primary].astype(float)
    if fallback is not None:
        forecast = forecast.fillna(test[fallback].astype(float))
    return forecast


def profile_forecast(
    history: pd.DataFrame, test: pd.DataFrame, label_gap_days: int, profile_days: int
) -> pd.Series:
    """Median price for this UK time of day, weekday or weekend, over recent known days.

    For target day D the decision day is D-1, so the newest day used is
    D - 1 - label_gap_days, and the window covers profile_days days up to it.
    """
    out = pd.Series(np.nan, index=test.index, dtype=float)
    history_day = history["settlement_date"]
    for day, rows in test.groupby("settlement_date"):
        newest = day - pd.Timedelta(days=1 + label_gap_days)
        oldest = newest - pd.Timedelta(days=profile_days - 1)
        window = history[(history_day >= oldest) & (history_day <= newest)]
        typical = window.groupby(PROFILE_KEYS)[TARGET].median()
        keys = pd.MultiIndex.from_frame(rows[PROFILE_KEYS])
        out.loc[rows.index] = typical.reindex(keys).to_numpy()
    return out
