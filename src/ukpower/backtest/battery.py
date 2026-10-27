"""Plan a battery's day: when to charge and discharge to make the most money.

Each half hour the battery either charges or discharges (never both; a switch per half hour
enforces this), within its power, its capacity, a daily cycle limit, and starting and ending
the day empty. Money is price x energy for each half hour, minus a wear cost per MWh
discharged. Prices in £/MWh, energy in MWh per half hour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from ukpower.config import BatterySettings

HALF_HOUR_H = 0.5


@dataclass(frozen=True)
class Schedule:
    """Energy charged from and delivered to the grid in each half hour, and the stored energy
    at the end of each half hour, all in MWh."""

    charge: np.ndarray
    discharge: np.ndarray
    stored: np.ndarray


def one_way_efficiency(battery: BatterySettings) -> float:
    """Losses are split evenly between charging and discharging."""
    return math.sqrt(battery.round_trip_efficiency)


def plan_day(prices: np.ndarray, battery: BatterySettings) -> Schedule:
    """The most profitable schedule for these prices.

    Raises:
        ValueError: if any price is missing.
        RuntimeError: if the solver fails.
    """
    prices = np.asarray(prices, dtype=float)
    if not np.all(np.isfinite(prices)):
        raise ValueError("every half hour needs a price to plan a day")
    n = len(prices)
    eta = one_way_efficiency(battery)
    step = battery.power_mw * HALF_HOUR_H  # most energy in one half hour
    cap = battery.capacity_mwh

    # Variables, in blocks of n: charge, discharge, stored, switch (1 = charging allowed)
    c, d, s, u = (slice(i * n, (i + 1) * n) for i in range(4))
    size = 4 * n

    # Minimise cost: pay price x charge, earn price x discharge, pay wear on discharge
    objective = np.zeros(size)
    objective[c] = prices
    objective[d] = -prices + battery.wear_cost_per_mwh

    rows, lower, upper = [], [], []

    def add(row: np.ndarray, lo: float, hi: float) -> None:
        rows.append(row)
        lower.append(lo)
        upper.append(hi)

    for t in range(n):
        # Stored energy: previous + charge x efficiency - discharge / efficiency
        row = np.zeros(size)
        row[s.start + t] = 1.0
        if t > 0:
            row[s.start + t - 1] = -1.0
        row[c.start + t] = -eta
        row[d.start + t] = 1.0 / eta
        add(row, 0.0, 0.0)  # starts empty, so the first half hour has no previous term

        # Charge only when the switch is on, discharge only when it is off
        row = np.zeros(size)
        row[c.start + t] = 1.0
        row[u.start + t] = -step
        add(row, -np.inf, 0.0)
        row = np.zeros(size)
        row[d.start + t] = 1.0
        row[u.start + t] = step
        add(row, -np.inf, step)

    # Ends the day empty
    row = np.zeros(size)
    row[s.start + n - 1] = 1.0
    add(row, 0.0, 0.0)

    # Daily cycle limit on energy delivered
    row = np.zeros(size)
    row[d] = 1.0
    add(row, -np.inf, battery.max_cycles_per_day * cap)

    low = np.zeros(size)
    high = np.concatenate([np.full(n, step), np.full(n, step), np.full(n, cap), np.ones(n)])
    integrality = np.concatenate([np.zeros(3 * n), np.ones(n)])

    result = milp(
        objective,
        constraints=LinearConstraint(np.array(rows), lower, upper),
        bounds=Bounds(low, high),
        integrality=integrality,
    )
    if not result.success:
        raise RuntimeError(f"battery schedule could not be solved: {result.message}")

    x = result.x
    clean = lambda v: np.where(np.abs(v) < 1e-9, 0.0, v)  # noqa: E731
    return Schedule(charge=clean(x[c]), discharge=clean(x[d]), stored=clean(x[s]))


def profit(schedule: Schedule, prices: np.ndarray, battery: BatterySettings) -> float:
    """Money made by following a schedule at these prices, after wear, in £."""
    prices = np.asarray(prices, dtype=float)
    trade = float(np.sum(prices * (schedule.discharge - schedule.charge)))
    return trade - battery.wear_cost_per_mwh * float(np.sum(schedule.discharge))
