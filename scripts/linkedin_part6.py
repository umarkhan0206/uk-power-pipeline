"""LinkedIn Part 6 image: the published price fell to £0, the settled price did not.

Run:
    python scripts/linkedin_part6.py
Saves data/linkedin_part6_fake_zeros.png (data/ is not committed).
"""

from __future__ import annotations

import sys
from zoneinfo import ZoneInfo

import pandas as pd
from matplotlib.dates import DateFormatter, DayLocator, HourLocator
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from ukpower.config import load_settings
from ukpower.forecast.data import load_gold

UK = ZoneInfo("Europe/London")
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
BAND = "#efede6"
SETTLED = "#2a78d6"
FEED = "#eb6834"


def main() -> int:
    settings = load_settings()
    gold = load_gold(settings.gold_path)

    # Whole period: every half hour with both a published and a settled price
    both = gold.dropna(subset=["feed_price", "target_price"])
    zero = both["feed_price"] == 0
    fake = zero & (both["target_price"].abs() > 0.005)
    n_zero, n_fake = int(zero.sum()), int(fake.sum())
    worst = both[fake]["target_price"].max()

    # The three days: 3 to 5 October 2026
    day = gold["settlement_date"]
    w = gold[(day >= "2026-10-03") & (day <= "2026-10-05")].copy()
    w["t"] = w["start_time_utc"].dt.tz_convert(UK)
    w_fake = w[(w["feed_price"] == 0) & (w["target_price"].abs() > 0.005)]
    top = w_fake.loc[w_fake["target_price"].idxmax()]
    half_hour = w["t"].iloc[1] - w["t"].iloc[0]

    print(
        f"Whole period: {len(both):,} half hours, {n_zero} at exactly £0, "
        f"{n_fake} not real, worst settled £{worst:.2f}"
    )
    print(
        f"3 to 5 Oct: {len(w_fake)} fake zeros; biggest {top['t']:%a %d %b %H:%M} "
        f"published £{top['feed_price']:.2f}, settled £{top['target_price']:.2f}"
    )

    fig = Figure(figsize=(7.2, 9), dpi=150, facecolor=SURFACE)

    # Headline: published in black, settled in the settled line's blue
    fig.text(0.07, 0.94, "The price said £0.", fontsize=26, weight="bold", color=TEXT)
    fig.text(
        0.07,
        0.895,
        f"The settled price said £{top['target_price']:.0f}.",
        fontsize=26,
        weight="bold",
        color=SETTLED,
    )
    fig.text(
        0.07,
        0.855,
        "Published imbalance price vs final settled price, GB, 3 to 5 October 2026 (UK time)",
        fontsize=9.5,
        color=TEXT_SECONDARY,
    )

    ax = fig.add_axes((0.1, 0.33, 0.86, 0.48), facecolor=SURFACE)
    for _, row in w_fake.iterrows():
        ax.axvspan(row["t"], row["t"] + half_hour, color=BAND, zorder=0)

    # Published first, settled on top: the orange only shows where it dives away
    ax.plot(w["t"], w["feed_price"], color=FEED, linewidth=2, label="Published price", zorder=3)
    ax.plot(
        w["t"],
        w["target_price"],
        color=SETTLED,
        linewidth=2,
        label="Settled price (what was paid)",
        zorder=4,
    )
    ax.scatter(
        w_fake["t"],
        w_fake["feed_price"],
        s=40,
        color=FEED,
        edgecolor=SURFACE,
        linewidth=1.5,
        zorder=5,
    )
    ax.vlines(
        top["t"],
        0,
        top["target_price"],
        colors=TEXT_SECONDARY,
        linestyles=":",
        linewidth=1.2,
        zorder=2,
    )
    ax.annotate(
        f"Published £{top['feed_price']:.2f}\nSettled £{top['target_price']:.2f}",
        xy=(top["t"], 0),
        xytext=(top["t"] + pd.Timedelta(hours=12), 232),
        fontsize=10,
        color=TEXT,
        weight="bold",
        va="center",
        bbox={"boxstyle": "round,pad=0.4", "facecolor": SURFACE, "edgecolor": GRID},
        arrowprops={
            "arrowstyle": "->",
            "color": TEXT_SECONDARY,
            "linewidth": 1.2,
            "connectionstyle": "arc3,rad=-0.2",
        },
        zorder=6,
    )

    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"£{v:,.0f}"))
    ax.xaxis.set_major_locator(DayLocator(tz=UK))
    ax.xaxis.set_major_formatter(DateFormatter("%a %d %b", tz=UK))
    ax.xaxis.set_minor_locator(HourLocator(byhour=[6, 12, 18], tz=UK))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
    ax.legend(loc="upper left", frameon=False, fontsize=9.5, labelcolor=TEXT)

    stats = [
        (f"{n_zero}", "published prices\nat exactly £0"),
        (f"{n_fake}", "of them\nweren't real"),
        (f"£{worst:.2f}", "real price behind\nthe worst one"),
        (f"{len(w_fake)}", "of the 30 in\njust three days"),
    ]
    for i, (number, label) in enumerate(stats):
        x = 0.07 + i * 0.235
        fig.text(x, 0.2, number, fontsize=22, weight="bold", color=FEED if i == 1 else TEXT)
        fig.text(x, 0.13, label, fontsize=9.5, color=TEXT_SECONDARY, linespacing=1.3)

    fig.text(
        0.07,
        0.04,
        "GB electricity market pipeline · Part 6 · Data: Elexon Insights, Oct 2024 to Oct 2026",
        fontsize=8,
        color=TEXT_SECONDARY,
    )

    out = settings.data_path / "linkedin_part6_fake_zeros.png"
    fig.savefig(out, facecolor=SURFACE)
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
