"""Turn a day's forecast into a battery schedule.

Forecast strategies plan with the forecast and are paid the settled price. The fixed rule
uses no forecast at all.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ukpower.backtest.battery import HALF_HOUR_H, Schedule, one_way_efficiency
from ukpower.config import BatterySettings, FixedRuleSettings

UK = "Europe/London"


def uk_hours(start_time_utc: pd.Series) -> np.ndarray:
    """UK clock time of each half hour as hours, for example 17.5 for 17:30."""
    local = start_time_utc.dt.tz_convert(UK)
    return (local.dt.hour + local.dt.minute / 60).to_numpy(dtype=float)


def filled_forecast(day: pd.DataFrame, column: str, fallback: str) -> tuple[np.ndarray, int]:
    """A forecast with every gap filled from a fallback forecast. Returns it and the gaps filled."""
    forecast = day[column].astype(float)
    gaps = int(forecast.isna().sum())
    return forecast.fillna(day[fallback].astype(float)).to_numpy(), gaps


def fixed_rule_schedule(
    hours: np.ndarray, rule: FixedRuleSettings, battery: BatterySettings
) -> Schedule:
    """Charge at full power in the charge hours, then give it all back in the discharge hours."""
    n = len(hours)
    step = battery.power_mw * HALF_HOUR_H
    eta = one_way_efficiency(battery)
    charging = np.isin(hours, rule.charge_uk_hours)
    discharging = np.isin(hours, rule.discharge_uk_hours)

    charge = np.where(charging, step, 0.0)
    # Never store more than the battery holds
    stored_in = min(charge.sum() * eta, battery.capacity_mwh)
    charge = charge * (stored_in / eta) / charge.sum() if charge.sum() else charge
    delivered = stored_in * eta
    slots = int(discharging.sum())
    discharge = np.where(discharging, delivered / slots, 0.0) if slots else np.zeros(n)
    if slots == 0:
        charge = np.zeros(n)  # nowhere to sell, so do nothing

    stored = np.cumsum(charge * eta - discharge / eta)
    return Schedule(
        charge=charge, discharge=discharge, stored=np.where(abs(stored) < 1e-9, 0, stored)
    )
