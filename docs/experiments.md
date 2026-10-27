# Forecasting experiments

Every model variant tried, why, and how it scored. All variants use the same walk forward
test: 13 monthly folds, October 2025 to 14 October 2026, retrained at the start of each
month, newest training day = decision day minus 2. All are scored on the same
17,938 half hours (98.6% of 18,192). Scores in £/MWh.

Target: the settled price (`target_price`). Settings were fixed in `config/settings.yaml`
before any LightGBM result was seen, and have not been changed since.

## Baselines (the bar)

| Method | MAE | RMSE |
|---|---|---|
| Same half hour 7 days ago (3 days ago if masked) | 44.53 | 63.36 |
| Same half hour 3 days ago | 45.18 | 64.42 |
| Typical price: median for UK time of day and weekday/weekend, last 28 known days | 33.30 | 47.20 |

## Variant 1: LightGBM direct

LightGBM (median objective) on 36 features, forecasting the price itself.

**Result:** MAE 32.87, RMSE 45.25. Beat the bar by 1.3%, but won only 7 of 13 months
and lost every month from July 2026.

**Diagnosis:** the average price rose from about £76 (October to December 2025) to
£130 to £138 (August to October 2026). The model's median forecast stayed at £84 to £107,
so it was too low by £34 to £40 from August. None of the features explain the overall
level, so a model trained on mostly cheaper history stays anchored to it. The baseline
follows the level because it only uses the last 28 days.

## Variant 2: LightGBM residual

Same model and settings, but it forecasts the distance from the 28 day typical price
(with that price added as a feature), and the forecast is typical price plus correction.
The baseline sets the level; LightGBM corrects the shape. Designed after seeing variant 1's
test results, based on the diagnosis above.

**Result:** MAE 30.22 (9.2% better than the bar), RMSE 41.60 (11.9% better).
Beat the bar in 11 of 13 months (lost November 2025 by £0.48 and May 2026 by £2.35).
Largest gains in the drifting months: September 2026 £44.60 vs £52.72.
Most used features after the typical price: wind forecast, then demand forecasts.
## Variant 3: ranges (quantile LightGBM on the residual set up)

Four more models per month at the 10, 25, 75 and 90% levels; the variant 2 median model
is the 50% level. Each half hour's five levels are sorted so they never cross
(needed on 1,646 of 18,192 half hours). Range baseline: the 10 to 90% levels of the same
28 day window as the typical price. Scored on 18,192 half hours.

| | Typical | LightGBM | Ideal |
|---|---|---|---|
| Inside the 10 to 90% band | 67.8% | 73.2% | 80% |
| Below the 10% line | 14.4% | 10.7% | 10% |
| Above the 90% line | 17.9% | 16.1% | 10% |
| Band width | £82.83 | £81.64 | |
| Pinball (average of 5 levels) | 12.00 | 10.56 | |

**Result:** narrower and better than the baseline (pinball 12% better), with a well
calibrated lower edge. But the band is too narrow on the upside: the price exceeds the
90% line 16.1% of the time, and in volatile months coverage falls to 52% (March 2026)
and 60% (September 2026). Quantile models are unstable (trees from 9 to 2,137).

## Variant 4: monthly calibration of the ranges

Each quantile model's practice run (trained without the last 28 training days) measures
its errors on those days; each level is shifted by the matching percentile of those errors
for the whole coming month. The test month is never used. Median unchanged.

| | LightGBM | Calibrated | Ideal |
|---|---|---|---|
| Inside the 10 to 90% band | 73.2% | 75.4% | 80% |
| Below the 90% line | 83.9% | 86.2% | 90% |
| Band width | £81.64 | £87.62 | |
| Pinball | 10.56 | 10.58 | |

**Result:** coverage improves by 2.3 points, but the band is 7% wider and pinball is
unchanged, so this is a trade of width for coverage, not a better range. Month by month
it helps after volatile spells (October 2026 88.4%, from 70.5%) and hurts after calm ones
(March 2026 44.8%, from 52.1%): a monthly shift reacts too slowly to changes in volatility.
**Not adopted as the headline.** Candidates: a daily adaptive calibration, and new
information about the market level (Elexon market index price).

## Variant 5: market index price (MID) features

New source `elexon_market_index` (APX traded price; no trades means missing). Three new
Gold features, all from days up to D-2 (about 10 hours before the 10:00 decision on D-1):
`mid_lag2` (same half hour), `mid_7d_mean` (level, D-8 to D-2, blank if under 300 traded
half hours) and `mid_7d_profile` (median for the UK time of day, same days). The model
settings are unchanged; all LightGBM models now see 40 features. Baselines unchanged.

| | Before MID | With MID |
|---|---|---|
| LightGBM direct, MAE | 32.87 | 30.79 |
| LightGBM residual, MAE | 30.22 | 29.83 |
| LightGBM residual, RMSE | 41.60 | 41.25 |
| Ranges: pinball | 10.56 | 10.33 |
| Ranges: inside 10 to 90% | 73.15% | 72.92% |
| Ranges: band width | £81.64 | £80.17 |

**Result:** MID carries the price level: the direct model improved by 6.3%, and its worst
month (October 2026) by £10.58. The residual model, which already took the level from the
28 day typical price, improved by 1.3%. Best model now: MAE 29.83 (10.4% better than the
bar), RMSE 41.25 (12.6% better), pinball 10.33 (13.9% better than the range baseline),
winning 11 of 13 months. `mid_7d_mean` ranks 4th by gain. Upside coverage is still too low.
Adding the most promising missing information gained about 1%, which suggests the residual
model is close to what is knowable at 10:00 the day before for this price.
## Notes

- The newest training targets are today's settled view, which in real life would not
  all have existed yet (see `docs/data_sources.md`); the 2 day gap limits this.
- Next: quantile forecasts (ranges) on top of variant 2, for the battery backtest.