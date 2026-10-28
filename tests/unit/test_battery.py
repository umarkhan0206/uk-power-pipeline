"""Tests for the battery scheduler: the physics and the money."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ukpower.backtest.battery import one_way_efficiency, plan_day, profit
from ukpower.config import BatterySettings

BATTERY = BatterySettings()  # 1 MW, 2 MWh, 88%, 2 cycles, £10 wear
STEP = 0.5  # MWh per half hour at 1 MW


def check_physics(schedule, battery: BatterySettings = BATTERY) -> None:  # noqa: ANN001
    eta = one_way_efficiency(battery)
    assert np.all(schedule.charge >= -1e-9) and np.all(schedule.charge <= STEP + 1e-9)
    assert np.all(schedule.discharge >= -1e-9) and np.all(schedule.discharge <= STEP + 1e-9)
    assert np.all(schedule.stored >= -1e-9) and np.all(
        schedule.stored <= battery.capacity_mwh + 1e-9
    )
    assert not np.any((schedule.charge > 1e-9) & (schedule.discharge > 1e-9))  # never both
    assert abs(schedule.stored[-1]) < 1e-6  # ends empty
    assert schedule.discharge.sum() <= battery.max_cycles_per_day * battery.capacity_mwh + 1e-6
    # energy balance: what went in, minus losses, came out
    assert math.isclose(schedule.charge.sum() * eta, schedule.discharge.sum() / eta, abs_tol=1e-6)


def test_flat_prices_mean_no_trading() -> None:
    schedule = plan_day(np.full(48, 80.0), BATTERY)
    assert schedule.charge.sum() == 0 and schedule.discharge.sum() == 0


def test_cheap_then_expensive_charges_then_discharges() -> None:
    prices = np.array([10.0] * 4 + [100.0] * 4)
    schedule = plan_day(prices, BATTERY)
    check_physics(schedule)
    assert math.isclose(schedule.charge[:4].sum(), 2.0, abs_tol=1e-6)  # full power, 4 half hours
    assert math.isclose(schedule.discharge.sum(), 1.76, abs_tol=1e-6)  # 2 MWh x 88%
    # earn 100 x 1.76, pay 10 x 2, wear 10 x 1.76
    assert math.isclose(profit(schedule, prices, BATTERY), 138.4, abs_tol=1e-6)


@pytest.mark.parametrize("n", [46, 48, 50])
def test_physics_hold_on_random_days_of_every_length(n: int) -> None:
    rng = np.random.default_rng(n)
    for _ in range(10):
        prices = rng.normal(90, 60, n)
        schedule = plan_day(prices, BATTERY)
        check_physics(schedule)
        assert profit(schedule, prices, BATTERY) >= -1e-6  # doing nothing is always possible


def test_very_negative_prices_never_charge_and_discharge_at_once() -> None:
    prices = np.array([-500.0] * 24 + [50.0] * 24)
    schedule = plan_day(prices, BATTERY)
    check_physics(schedule)
    assert profit(schedule, prices, BATTERY) > 0


def test_cycle_limit_is_respected() -> None:
    one_cycle = BatterySettings(max_cycles_per_day=0.5)
    prices = np.tile([0.0, 0.0, 200.0, 200.0], 12)
    schedule = plan_day(prices, one_cycle)
    check_physics(schedule, one_cycle)
    assert schedule.discharge.sum() <= 1.0 + 1e-6


def test_missing_price_is_refused() -> None:
    with pytest.raises(ValueError, match="needs a price"):
        plan_day(np.array([10.0, np.nan, 100.0]), BATTERY)


def test_without_sell_prices_planning_is_unchanged() -> None:
    prices = np.array([10.0] * 4 + [100.0] * 4)
    a, b = plan_day(prices, BATTERY), plan_day(prices, BATTERY, sell_prices=prices)
    assert np.array_equal(a.charge, b.charge) and np.array_equal(a.discharge, b.discharge)


def test_careful_planning_skips_a_trade_that_only_pays_on_the_middle_guess() -> None:
    middle = np.array([10.0] * 4 + [100.0] * 4)
    buy = np.array([60.0] * 4 + [130.0] * 4)  # buying might cost more
    sell = np.array([5.0] * 4 + [70.0] * 4)  # selling might earn less: 70 x 0.88 - wear < 60
    assert plan_day(middle, BATTERY).discharge.sum() > 0
    careful = plan_day(buy, BATTERY, sell_prices=sell)
    assert careful.charge.sum() == 0 and careful.discharge.sum() == 0


def test_buy_and_sell_prices_must_match_in_length() -> None:
    with pytest.raises(ValueError, match="same half hours"):
        plan_day(np.ones(4), BATTERY, sell_prices=np.ones(3))
