# Battery backtest

A simulated 1 MW / 2 MWh battery (88% round trip, at most 2 cycles a day, £10 wear per MWh
discharged, empty at the start and end of every day). Every day at 10:00 it plans tomorrow's
48 half hours from a forecast (a mixed integer program: never charging and discharging at
once), the plan is locked, and it is paid the settled imbalance price. 379 days,
1 October 2025 to 14 October 2026, the same walk forward forecasts as `docs/experiments.md`.

| Strategy | Profit | Share of perfect foresight | Without the 5 best days | Losing days |
|---|---|---|---|---|
| Perfect foresight (ceiling) | £94,413 | 100% | 100% | 0 |
| LightGBM residual forecast | £28,965 | 30.7% | 29.6% | 108 |
| Typical price (28 days) | £21,019 | 22.3% | 21.2% | 136 |
| Fixed rule (charge 02:00 to 04:00, discharge 17:00 to 19:00 UK) | £10,638 | 11.3% | 10.5% | 139 |
| Same half hour 7 days ago | £79 | 0.1% | negative | 217 |

**Result:** the LightGBM forecast earned 38% more than the best simple forecast and
2.7 times the fixed rule, and beat the typical price forecast in 12 of 13 months.
The ranking holds without the 5 best days.

**Findings:** the 7 days ago forecast is only 34% worse on MAE than the typical price, but
earns almost nothing: single past days carry random spikes that send the battery the wrong
way. The fixed rule is competitive in winter (it beat LightGBM in October 2025, December
and February), when the evening peak is reliable, and loses money in May and July.

**Limits:** imbalance price only (no day ahead, intraday or grid service revenue), the plan
is not adjusted during the day, no fees or network charges, and the battery is assumed too
small to move prices. Perfect foresight is unreachable at 10:00 the day before, so the
comparison that matters is against the alternatives. The settled price is used for payment;
the newest days may still be revised slightly.

Run: `python -m ukpower.backtest.run` (after `python -m ukpower.forecast.evaluate`).

## Pre-registered: careful trading with ranges

Decided on 27 October 2026, before running it. Plan charging with the 75% level and
discharging with the 25% level of the LightGBM ranges (variant 5 quantile models, not
calibrated), so the battery only trades when it pays even if buying turns out dearer and
selling cheaper than the middle forecast. No other setting changes. Run once and reported
whatever the result. It counts as better if total profit is higher, or if losing days fall
clearly with similar profit.