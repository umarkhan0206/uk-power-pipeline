"""Charts for the README, rebuilt from the backtest results, never edited by hand.

Run (after python -m ukpower.backtest.run):
    python scripts/make_charts.py
"""

from __future__ import annotations

import sys

import pandas as pd
from matplotlib.dates import DateFormatter, MonthLocator
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from ukpower.config import PROJECT_ROOT, load_settings

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
#: column, label, colour (fixed order from a colour blind safe palette)
SERIES = [
    ("lgbm", "LightGBM forecast", "#2a78d6"),
    ("typical_28d", "Typical price forecast", "#eb6834"),
    ("fixed_rule", "Fixed daily rule", "#1baf7a"),
    ("same_hh_7d", "Same half hour, 7 days ago", "#eda100"),
]


def cumulative_profit_chart(daily: pd.DataFrame) -> Figure:
    """Running total profit per strategy across the test year."""
    cumulative = daily[[key for key, _, _ in SERIES]].sort_index().cumsum()
    perfect = daily["perfect_foresight"].sum()

    fig = Figure(figsize=(9, 5.2), dpi=150, facecolor=SURFACE)
    ax = fig.add_axes((0.08, 0.1, 0.78, 0.7), facecolor=SURFACE)
    for key, label, colour in SERIES:
        ax.plot(cumulative.index, cumulative[key], color=colour, linewidth=2, label=label)
        end = cumulative[key].iloc[-1]
        ax.annotate(
            f"£{end:,.0f}",
            xy=(cumulative.index[-1], end),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
            color=TEXT,
        )

    ax.yaxis.set_major_formatter(
        FuncFormatter(
            lambda v, _: "£0" if v == 0 else f"{'−' if v < 0 else ''}£{abs(v) / 1000:,.0f}k"
        )
    )
    ax.xaxis.set_major_locator(MonthLocator(bymonth=[1, 4, 7, 10]))
    ax.xaxis.set_major_formatter(DateFormatter("%b %Y"))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=TEXT)

    fig.text(
        0.08,
        0.93,
        "What a 1 MW / 2 MWh battery would have earned, Oct 2025 to Oct 2026",
        fontsize=12,
        color=TEXT,
        weight="bold",
    )
    fig.text(
        0.08,
        0.875,
        "Planned at 10:00 the day before, paid the settled imbalance price. "
        f"Perfect foresight (the ceiling) reached £{perfect:,.0f}.",
        fontsize=9,
        color=TEXT_SECONDARY,
    )
    return fig


def main() -> int:
    settings = load_settings()
    daily = pd.read_parquet(settings.data_path / "results" / "backtest_daily.parquet")
    out = PROJECT_ROOT / "docs" / "img" / "backtest_cumulative.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cumulative_profit_chart(daily).savefig(out, facecolor=SURFACE)
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
