"""Simple forecasts that need no machine learning. A model must beat these to be worth using.

Every baseline uses only what was known at the decision: the lag features were masked by
publish time in Gold, and the typical price profile obeys the same label gap as training.
"""

from __future__ import annotations

from collections.abc import Sequence

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


def _window(
    history: pd.DataFrame, day: pd.Timestamp, label_gap_days: int, profile_days: int
) -> pd.DataFrame:
    """Known days for target day D: decision day is D-1, newest day is D-1-gap."""
    newest = day - pd.Timedelta(days=1 + label_gap_days)
    oldest = newest - pd.Timedelta(days=profile_days - 1)
    days = history["settlement_date"]
    return history[(days >= oldest) & (days <= newest)]


def profile_forecast(
    history: pd.DataFrame, test: pd.DataFrame, label_gap_days: int, profile_days: int
) -> pd.Series:
    """Median price for this UK time of day, weekday or weekend, over recent known days."""
    out = pd.Series(np.nan, index=test.index, dtype=float)
    for day, rows in test.groupby("settlement_date"):
        window = _window(history, day, label_gap_days, profile_days)
        typical = window.groupby(PROFILE_KEYS)[TARGET].median()
        keys = pd.MultiIndex.from_frame(rows[PROFILE_KEYS])
        out.loc[rows.index] = typical.reindex(keys).to_numpy()
    return out


def profile_quantiles(
    history: pd.DataFrame,
    test: pd.DataFrame,
    label_gap_days: int,
    profile_days: int,
    quantiles: Sequence[float],
) -> pd.DataFrame:
    """Like profile_forecast, but several levels (for example 10% and 90%) of the same window.

    Returns one column per level, named by the level itself.
    """
    levels = list(quantiles)
    out = pd.DataFrame(np.nan, index=test.index, columns=levels, dtype=float)
    for day, rows in test.groupby("settlement_date"):
        window = _window(history, day, label_gap_days, profile_days)
        if window.empty:
            continue
        table = window.groupby(PROFILE_KEYS)[TARGET].quantile(levels).unstack()
        keys = pd.MultiIndex.from_frame(rows[PROFILE_KEYS])
        out.loc[rows.index, levels] = table.reindex(keys)[levels].to_numpy()
    return out
