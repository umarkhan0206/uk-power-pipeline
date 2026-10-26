"""Load the Gold feature table and check it before any forecasting.

Gold stores times in UTC without a timezone label. Here they are labelled as UTC,
so later code can never mistake them for UK local time.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

KEY_COLUMNS = ("settlement_date", "settlement_period")
TIME_COLUMNS = ("start_time_utc", "decision_time_utc")
TARGET = "target_price"

#: Never model inputs: identifiers, the answer, and values only known after the decision.
NOT_FEATURES = frozenset(
    {
        *KEY_COLUMNS,
        *TIME_COLUMNS,
        TARGET,
        "target_run_type",
        "target_is_settled",
        "target_is_final",
        "feed_price",
    }
)

REQUIRED_COLUMNS = (
    *KEY_COLUMNS,
    *TIME_COLUMNS,
    TARGET,
    "feed_price",
    "price_lag3",
    "price_lag7",
    "uk_hour",
    "is_weekend",
)


class GoldDataError(ValueError):
    """The Gold table is missing columns or breaks a rule the forecaster relies on."""


def load_gold(path: Path) -> pd.DataFrame:
    """Read the Gold export and return it checked, typed and sorted by time.

    Raises:
        FileNotFoundError: if the file does not exist.
        GoldDataError: if the table fails a check.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Gold export not found: {path}")
    frame = prepare(pd.read_parquet(path))
    logger.info(
        "Loaded Gold: %d rows, %d target days (%s to %s), %d features",
        len(frame),
        frame["settlement_date"].nunique(),
        frame["settlement_date"].min().date(),
        frame["settlement_date"].max().date(),
        len(feature_columns(frame)),
    )
    return frame


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """Check and type a Gold table. Returns a new, time sorted frame.

    Raises:
        GoldDataError: on missing columns, duplicate half hours, or a decision
            that is not before its half hour.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise GoldDataError(f"Gold is missing columns {missing}")

    out = frame.copy()
    for column in TIME_COLUMNS:
        out[column] = pd.to_datetime(out[column], utc=True)
    out["settlement_date"] = pd.to_datetime(out["settlement_date"])

    duplicates = int(out.duplicated(list(KEY_COLUMNS)).sum())
    if duplicates:
        raise GoldDataError(f"Gold has {duplicates} duplicate half hours")

    late = int((out["decision_time_utc"] >= out["start_time_utc"]).sum())
    if late:
        raise GoldDataError(f"{late} rows have a decision at or after their half hour starts")

    return out.sort_values("start_time_utc").reset_index(drop=True)


def feature_columns(frame: pd.DataFrame) -> list[str]:
    """Columns the model may use: everything except identifiers, the answer and later values."""
    return [c for c in frame.columns if c not in NOT_FEATURES]
