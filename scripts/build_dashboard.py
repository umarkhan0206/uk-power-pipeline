"""Build the one page dashboard from the published results.

Writes docs/dashboard/index.html: one self contained page with the data embedded, served by
GitHub Pages. No server, no sleeping app, nothing typed by hand.

Run (after python scripts/publish_results.py):
    python scripts/build_dashboard.py
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from ukpower.config import BatterySettings, load_settings

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
TEMPLATE = ROOT / "dashboard" / "template.html"
OUT = ROOT / "docs" / "dashboard" / "index.html"
PLACEHOLDER = "/*__DATA__*/null"
STRATEGIES = ["perfect_foresight", "lgbm", "typical_28d", "fixed_rule", "same_hh_7d"]
UK = "Europe/London"


def rounded(values: Iterable[float], digits: int) -> list[float | None]:
    return [None if pd.isna(v) else round(float(v), digits) for v in values]


def build_data(forecasts: pd.DataFrame, daily: pd.DataFrame, battery: BatterySettings) -> dict:
    """Everything the page shows, from the published results and the battery settings."""
    forecasts = forecasts.copy()
    forecasts["start_time_utc"] = pd.to_datetime(forecasts["start_time_utc"], utc=True)
    forecasts["settlement_date"] = pd.to_datetime(forecasts["settlement_date"])
    daily = daily.copy()
    daily.index = pd.to_datetime(daily.index)
    daily = daily.sort_index()

    scored = forecasts.dropna(subset=["target_price"])
    errors = pd.DataFrame(
        {
            "month": scored["settlement_date"].dt.strftime("%Y-%m"),
            "lgbm": (scored["lgbm_residual"] - scored["target_price"]).abs(),
            "typical": (scored["typical_28d"] - scored["target_price"]).abs(),
        }
    )
    mae = {
        "lgbm": float(errors["lgbm"].mean()),
        "typical": float(errors["typical"].mean()),
        "n": len(scored),
    }
    by_month = errors.groupby("month")[["lgbm", "typical"]].mean()
    mae_monthly = {
        "months": list(by_month.index),
        "lgbm": rounded(by_month["lgbm"], 2),
        "typical": rounded(by_month["typical"], 2),
    }

    days = {}
    for day, group in forecasts.sort_values("start_time_utc").groupby("settlement_date"):
        if day not in daily.index:
            continue
        local = group["start_time_utc"].dt.tz_convert(UK)
        days[f"{day:%Y-%m-%d}"] = {
            "t": local.dt.strftime("%H:%M").tolist(),
            "a": rounded(group["target_price"], 2),
            "f": rounded(group["lgbm_residual"], 2),
            "lo": rounded(group["lgbm_q10"], 2),
            "hi": rounded(group["lgbm_q90"], 2),
            "pl": rounded(group["plan_lgbm"], 3),
            "pp": rounded(group["plan_perfect"], 3),
        }

    return {
        "start": f"{daily.index.min():%Y-%m-%d}",
        "end": f"{daily.index.max():%Y-%m-%d}",
        "totals": {s: round(float(daily[s].sum()), 2) for s in STRATEGIES},
        "battery": battery.model_dump(),
        "mae": mae,
        "mae_monthly": mae_monthly,
        "daily": {
            "dates": [f"{d:%Y-%m-%d}" for d in daily.index],
            **{s: rounded(daily[s], 2) for s in STRATEGIES},
        },
        "days": days,
    }


def render(data: dict, template: str) -> str:
    """Put the data into the page. '</' is escaped so the data can never close the script."""
    if PLACEHOLDER not in template:
        raise ValueError(f"template has no {PLACEHOLDER} placeholder")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return template.replace(PLACEHOLDER, payload)


def main() -> int:
    forecasts = pd.read_parquet(RESULTS / "forecasts.parquet")
    daily = pd.read_parquet(RESULTS / "backtest_daily.parquet")
    data = build_data(forecasts, daily, load_settings().backtest.battery)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(data, TEMPLATE.read_text(encoding="utf-8")), encoding="utf-8")
    print(
        f"Saved {OUT.relative_to(ROOT)}: {len(data['days'])} days, "
        f"{OUT.stat().st_size / 1024:,.0f} KB"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
