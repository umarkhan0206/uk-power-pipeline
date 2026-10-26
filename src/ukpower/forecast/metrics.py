"""Forecast error scores, in £/MWh."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


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
