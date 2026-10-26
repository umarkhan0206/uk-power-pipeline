"""Tests for the walk forward folds."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from ukpower.forecast.splits import Fold, monthly_folds, split

FIRST, LAST = date(2024, 10, 1), date(2026, 10, 14)
TEST_START = date(2025, 10, 1)


def test_one_fold_per_month_with_a_partial_last_month() -> None:
    folds = monthly_folds(FIRST, LAST, TEST_START, label_gap_days=2)
    assert len(folds) == 13  # October 2025 to October 2026
    assert folds[0] == Fold(date(2025, 9, 28), date(2025, 10, 1), date(2025, 10, 31))
    assert folds[1].test_end == date(2025, 11, 30)
    assert folds[-1] == Fold(date(2026, 9, 28), date(2026, 10, 1), date(2026, 10, 14))


def test_test_months_follow_each_other_without_gaps_or_overlaps() -> None:
    folds = monthly_folds(FIRST, LAST, TEST_START, label_gap_days=2)
    for earlier, later in zip(folds, folds[1:], strict=False):
        assert later.test_start == earlier.test_end + timedelta(days=1)


def test_newest_training_day_respects_the_gap() -> None:
    for gap in (1, 2, 5):
        for fold in monthly_folds(FIRST, LAST, TEST_START, label_gap_days=gap):
            decision_day = fold.test_start - timedelta(days=1)
            assert fold.train_end == decision_day - timedelta(days=gap)


def test_gap_of_one_ends_on_the_last_finished_day() -> None:
    fold = monthly_folds(FIRST, LAST, TEST_START, label_gap_days=1)[0]
    assert fold.train_end == date(2025, 9, 29)  # 30 September is still running at 10:00


@pytest.mark.parametrize(
    ("first", "test_start", "gap", "message"),
    [
        (FIRST, TEST_START, 0, "at least 1"),
        (FIRST, date(2025, 10, 2), 2, "first day of a month"),
        (FIRST, date(2026, 11, 1), 2, "no target days"),
        (date(2025, 9, 29), TEST_START, 2, "train on nothing"),
    ],
)
def test_bad_settings_are_refused(first: date, test_start: date, gap: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        monthly_folds(first, LAST, test_start, label_gap_days=gap)


def test_split_keeps_training_before_the_gap_and_test_inside_the_month() -> None:
    days = pd.date_range("2025-09-25", "2025-10-03", freq="D")
    frame = pd.DataFrame({"settlement_date": days, "value": range(len(days))})
    fold = Fold(date(2025, 9, 28), date(2025, 10, 1), date(2025, 10, 2))

    train, test = split(frame, fold)
    assert train["settlement_date"].max() == pd.Timestamp("2025-09-28")
    assert list(test["settlement_date"]) == [pd.Timestamp("2025-10-01"), pd.Timestamp("2025-10-02")]
    assert not set(train["settlement_date"]) & set(test["settlement_date"])
