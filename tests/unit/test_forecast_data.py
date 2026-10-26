"""Tests for loading and checking the Gold table."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ukpower.forecast.data import (
    NOT_FEATURES,
    GoldDataError,
    feature_columns,
    load_gold,
    prepare,
)


def make_gold(periods: int = 4) -> pd.DataFrame:
    """A tiny Gold table: one target day, decision 10:00 UTC the day before."""
    start = pd.date_range("2025-01-02 00:00", periods=periods, freq="30min")
    return pd.DataFrame(
        {
            "settlement_date": pd.Timestamp("2025-01-02"),
            "settlement_period": range(1, periods + 1),
            "start_time_utc": start,
            "decision_time_utc": pd.Timestamp("2025-01-01 10:00"),
            "target_price": [50.0, 60.0, 70.0, 80.0][:periods],
            "target_run_type": "RF",
            "target_is_settled": True,
            "target_is_final": True,
            "feed_price": [50.0, 60.0, 0.0, 80.0][:periods],
            "price_lag3": 55.0,
            "price_lag7": 45.0,
            "uk_hour": [0.0, 0.5, 1.0, 1.5][:periods],
            "is_weekend": 0,
            "fc_wind_mw": 9000.0,
        }
    ).iloc[::-1]  # deliberately out of order


def test_times_are_labelled_utc_and_rows_sorted() -> None:
    out = prepare(make_gold())
    assert str(out["start_time_utc"].dt.tz) == "UTC"
    assert str(out["decision_time_utc"].dt.tz) == "UTC"
    assert out["start_time_utc"].is_monotonic_increasing
    assert out.loc[0, "start_time_utc"] == pd.Timestamp("2025-01-02 00:00", tz="UTC")


def test_prepare_does_not_change_the_input() -> None:
    gold = make_gold()
    prepare(gold)
    assert gold["start_time_utc"].dt.tz is None


def test_missing_column_is_rejected() -> None:
    with pytest.raises(GoldDataError, match="missing columns \\['feed_price'\\]"):
        prepare(make_gold().drop(columns="feed_price"))


def test_duplicate_half_hour_is_rejected() -> None:
    gold = make_gold()
    with pytest.raises(GoldDataError, match="1 duplicate half hours"):
        prepare(pd.concat([gold, gold.iloc[[0]]]))


def test_decision_at_or_after_the_half_hour_is_rejected() -> None:
    gold = make_gold()
    gold["decision_time_utc"] = pd.Timestamp("2025-01-02 00:30")
    with pytest.raises(GoldDataError, match="2 rows have a decision"):
        prepare(gold)


def test_features_exclude_the_answer_and_later_values() -> None:
    features = feature_columns(prepare(make_gold()))
    assert "target_price" not in features
    assert "feed_price" not in features
    assert "target_is_final" not in features
    assert set(features) == {"price_lag3", "price_lag7", "uk_hour", "is_weekend", "fc_wind_mw"}
    assert not NOT_FEATURES & set(features)


def test_load_gold_reads_parquet(tmp_path: Path) -> None:
    path = tmp_path / "gold.parquet"
    make_gold().to_parquet(path)
    assert len(load_gold(path)) == 4


def test_load_gold_missing_file_is_clear(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Gold export not found"):
        load_gold(tmp_path / "nope.parquet")
