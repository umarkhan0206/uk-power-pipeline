"""Forecast error scores, in £/MWh."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike


@dataclass(frozen=True)
class Score:
    """How far off a forecast was. n is the number of half hours scored."""

    n: int
    mae: float
    rmse: float


def score(actual: pd.Series, forecast: pd.Series) -> Score:
    """MAE (average size of the error) and RMSE (punishes big misses more).

    Half hours where either value is missing are left out, and counted in n.

    Raises:
        ValueError: if the two series do not line up, or nothing is left to score.
    """
    if not actual.index.equals(forecast.index):
        raise ValueError("actual and forecast must have the same index")
    both = actual.notna() & forecast.notna()
    if not both.any():
        raise ValueError("nothing to score: no half hour has both an actual and a forecast")
    error = forecast[both].to_numpy(dtype=float) - actual[both].to_numpy(dtype=float)
    return Score(
        n=int(both.sum()),
        mae=float(np.mean(np.abs(error))),
        rmse=float(np.sqrt(np.mean(error**2))),
    )


def pinball(actual: ArrayLike, forecast: ArrayLike, alpha: float) -> float:
    """Average quantile (pinball) loss for one level alpha. Lower is better.

    A forecast for the 90% level is punished 9 times more for being too low than too high,
    so the best forecast is the value the price stays below 90% of the time.
    Pairs with a missing value are left out.

    Raises:
        ValueError: if nothing is left to score.
    """
    a = np.asarray(actual, dtype=float)
    f = np.asarray(forecast, dtype=float)
    both = ~(np.isnan(a) | np.isnan(f))
    if not both.any():
        raise ValueError("nothing to score")
    diff = a[both] - f[both]
    return float(np.mean(np.maximum(alpha * diff, (alpha - 1) * diff)))
