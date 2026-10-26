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
## Notes

- The newest training targets are today's settled view, which in real life would not
  all have existed yet (see `docs/data_sources.md`); the 2 day gap limits this.
- Next: quantile forecasts (ranges) on top of variant 2, for the battery backtest.