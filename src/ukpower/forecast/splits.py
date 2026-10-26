"""Walk forward folds: retrain at the start of each month, then forecast that month.

All dates are UK target days (settlement dates), inclusive. The first test day of a fold
is decided at 10:00 on the day before it (the decision day). That day is still running,
so the newest training day is the decision day minus label_gap_days (at least 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd


@dataclass(frozen=True)
class Fold:
    """One retrain: learn from target days up to train_end, forecast test_start to test_end."""

    train_end: date
    test_start: date
    test_end: date

    def __post_init__(self) -> None:
        if not self.train_end < self.test_start <= self.test_end:
            raise ValueError(f"invalid fold {self}")


def _next_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def monthly_folds(
    first_day: date, last_day: date, test_start: date, label_gap_days: int
) -> list[Fold]:
    """One fold per calendar month from test_start to last_day. The last may be partial.

    Raises:
        ValueError: if test_start is not the 1st of a month, leaves no training data,
            is after the data, or label_gap_days is below 1.
    """
    if label_gap_days < 1:
        raise ValueError("label_gap_days must be at least 1: the decision day is still running")
    if test_start.day != 1:
        raise ValueError(f"test_start must be the first day of a month, got {test_start}")
    if test_start > last_day:
        raise ValueError(f"no target days on or after test_start {test_start}")

    folds = []
    start = test_start
    while start <= last_day:
        end = min(_next_month(start) - timedelta(days=1), last_day)
        decision_day = start - timedelta(days=1)
        folds.append(
            Fold(
                train_end=decision_day - timedelta(days=label_gap_days),
                test_start=start,
                test_end=end,
            )
        )
        start = _next_month(start)

    if folds[0].train_end < first_day:
        raise ValueError(
            f"first fold would train on nothing: data starts {first_day}, "
            f"training must end by {folds[0].train_end}"
        )
    return folds


def split(frame: pd.DataFrame, fold: Fold) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Training rows (target days up to train_end) and test rows (the fold's month)."""
    day = frame["settlement_date"].dt.date
    train = frame[day <= fold.train_end]
    test = frame[(day >= fold.test_start) & (day <= fold.test_end)]
    return train, test
