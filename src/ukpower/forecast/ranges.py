"""Range (quantile) forecasts: keep each half hour's levels in order, and score them."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ukpower.forecast.data import TARGET
from ukpower.forecast.metrics import pinball

QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)


def qcol(prefix: str, q: float) -> str:
    """Column name for one level, for example qcol("lgbm", 0.1) -> "lgbm_q10"."""
    return f"{prefix}_q{round(q * 100):02d}"


def quantile_columns(prefix: str) -> list[str]:
    return [qcol(prefix, q) for q in QUANTILES]


def sort_quantiles(forecasts: pd.DataFrame, prefix: str) -> tuple[pd.DataFrame, int]:
    """Put each half hour's levels in increasing order. Returns the frame and how many crossed.

    Separately trained models can disagree (a 10% level above the 25% level), which is
    impossible for real ranges. Rows with a missing level are left as they are.
    """
    columns = quantile_columns(prefix)
    values = forecasts[columns].to_numpy(dtype=float)
    complete = np.isfinite(values).all(axis=1)
    ordered = values.copy()
    ordered[complete] = np.sort(values[complete], axis=1)
    crossed = int((complete & (ordered != values).any(axis=1)).sum())
    out = forecasts.copy()
    out[columns] = ordered
    return out, crossed


def range_scores(frame: pd.DataFrame, prefix: str) -> dict[str, float]:
    """Coverage of each level, how often the price falls inside the bands, width, pinball."""
    y = frame[TARGET].to_numpy(dtype=float)
    q = {level: frame[qcol(prefix, level)].to_numpy(dtype=float) for level in QUANTILES}
    scores: dict[str, float] = {"n": float(len(y))}
    for level in QUANTILES:
        scores[f"below_q{round(level * 100):02d}_pct"] = 100 * float(np.mean(y < q[level]))
    scores["inside_10_90_pct"] = 100 * float(np.mean((y >= q[0.1]) & (y <= q[0.9])))
    scores["inside_25_75_pct"] = 100 * float(np.mean((y >= q[0.25]) & (y <= q[0.75])))
    scores["width_10_90"] = float(np.mean(q[0.9] - q[0.1]))
    scores["pinball"] = float(np.mean([pinball(y, q[level], level) for level in QUANTILES]))
    return scores


def range_summary(
    forecasts: pd.DataFrame, prefixes: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Overall and per month range scores, every method on the same half hours."""
    needed = [TARGET, *[c for p in prefixes for c in quantile_columns(p)]]
    fair = forecasts[forecasts[needed].notna().all(axis=1)]
    overall = pd.DataFrame({p: range_scores(fair, p) for p in prefixes}).T
    rows = []
    for month, group in fair.groupby("month"):
        row: dict[str, object] = {"month": month}
        for p in prefixes:
            s = range_scores(group, p)
            row[f"{p}_inside_10_90"] = s["inside_10_90_pct"]
            row[f"{p}_width"] = s["width_10_90"]
        rows.append(row)
    return overall.round(2), pd.DataFrame(rows).set_index("month").round(1)
