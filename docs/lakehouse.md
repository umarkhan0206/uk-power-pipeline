# Lakehouse (Databricks Free Edition)

## Layout

* Catalog `workspace`, schemas `bronze`, `silver`, `gold` (see `databricks/00_setup.sql`)
* Volume `workspace.bronze.landing` holds the landing zone files
* Notebooks run in order: `00_setup.sql`, `01_unpack_landing`, `02_bronze`, `03_silver`,
  `04_gold_build`

## Moving the landing zone

16,346 files (293 MB) were zipped to 27.6 MB, uploaded to the volume and unpacked.
Windows zip paths use backslashes, so paths were normalised while unpacking.
`bronze.landing_files` recomputes the SHA256 of all 8,173 data files and compares it
with the fingerprint in each file name: 0 mismatches, so every file arrived byte for byte.

New sources are moved the same way: zip one source folder, upload, unpack into
`landing/files`. The unpack step never overwrites a file that already exists.

## Bronze: data as received

| Table | Rows | Notes |
|---|---|---|
| elexon_system_prices | 35,663 | 743 days |
| elexon_settled_prices | 36,143 | 753 days, latest settlement run per half hour |
| elexon_demand_outturn | 35,664 | |
| elexon_generation_by_fuel | 713,280 | 20 fuel types per half hour |
| elexon_wind_solar_actual | 108,963 | includes republished versions |
| elexon_wind_solar_day_ahead | 106,416 | 738 days with data |
| elexon_demand_forecast | 93,109 | 2,233 files (several issues per day) |
| elexon_wind_forecast | 53,928 | hourly |
| carbon_intensity | 36,407 | one extra window per file |
| open_meteo_ecmwf | 742 files | 12 locations; 72 to 168 hours per file; real run time from metadata |
| landing_files | 8,928 | one row per data file, including empty ones; 10 sources, 293.8 MB |

Every row keeps `_source_file`, `_partition_day`, `_fetched_at` and `_ingested_at`.

Found in Bronze:
* 13 Oct 2026 system prices has 47 of 48 periods: the backfill fetched it at 23:00 UTC,
  before the last price was published. The incremental refetch heals this.
* 3 day ahead files are empty (`"data": []`): 2025-02-09, 2026-01-09, 2026-03-05.
  `explode` drops them silently, which is why `landing_files` exists.
* Settled prices: 36,143 rows from 753 files (1 October 2024 to 23 October 2026), 0 rows
  whose date differs from their file's day. One half hour short of 753 × 48:
  23 October period 48 was not yet published at download time; the next run fills it.
* `landing_files` after adding settled prices: 8,928 data files, 3 empty (the known day
  ahead ones), 0 bad fingerprints.

## Silver: typed, one row per record, point in time correct

Measured data keeps the latest version per key. Forecasts keep the latest version
published at or before the decision time (10:00 UK on the day before the target day).

| Table | Rows | Key checks |
|---|---|---|
| system_prices | 35,663 | unique per half hour |
| settled_prices | 36,143 | unique per half hour, sell equals buy in every row |
| settled_price_runs | 36,143 | one row per half hour per run (grows each Sunday) |
| demand_outturn | 35,664 | 0 incomplete days |
| generation_by_fuel | 713,280 | 0 incomplete days |
| wind_solar_actual | 104,652 | 780 half hours missing at source |
| carbon_intensity | 35,664 | 0 label mismatches with Elexon |
| demand_forecast_asof | 35,712 | 0 rows published after 10:00 |
| wind_forecast_asof | 17,856 | 0 rows published after 10:00 |
| weather_forecast_asof | 222,600 | target day plus one extra hour; run plus 6 h before 10:00 on every row, 0 blank values |
| wind_solar_day_ahead_asof | 105,984 | as known at 19:00, comparison only |

### System prices
* Sell and buy prices are identical in all 35,663 half hours (single imbalance price).
* Range -£97.92 to £2,900 per MWh. Median £91.50, mean £90.45.
* 1,950 negative half hours, mostly around midday on weekends and holidays.
* 29 half hours above £500. All eight half hours at £2,900 are on 8 Jan 2025,
  15:00 to 19:00, with a short system (positive net imbalance volume).
  The backtest must report results with and without this day.
* This is the market feed. It differs from the settled price in 1,521 of 35,663 half
  hours, and 30 of its 177 zero prices are not real (see `docs/data_sources.md`,
  Elexon settled prices).

### Settled prices
* `settled_prices`: one row per half hour, the newest download from Bronze. Columns:
  `settlement_date`, `settlement_period`, `run_type`, `system_sell_price`,
  `system_buy_price`, `is_settled` (any official run, so not BM), `is_final` (RF or DF),
  `_fetched_at`, `_source_file`.
* First build: 239 not settled yet (BM), 19,632 settled but not final, 16,272 final.
* `settled_price_runs`: one row per half hour per run, with `first_seen_at`, the first
  download in which that run appeared. After the first load every half hour has one run.
  Each Sunday look back adds a row whenever a half hour moves to a newer run, so this
  table is the revision history by run.
* Compared with `system_prices` on 35,663 shared half hours: 1,521 changed, 241 up and
  91 down by more than £10; 177 feed zeros, of which 147 real and 30 not.

### Wind and solar actuals
* 4,311 superseded versions removed (latest publish time wins).
* 780 half hours are missing at the source across 60 days, including
  20 May to 24 Jun 2026 with 16 to 24 half hours missing every day.
  A refetch on 17 Oct 2026 returned exactly the same gaps, so they are permanent.

### Two measures of wind
Generation by fuel `WIND` and wind and solar actuals (onshore plus offshore) differ:
* Average 8,110 MW against 9,318 MW (offshore alone averages 5,428 MW).
* Correlation 0.9442, best at zero time shift (timing ruled out).
* In 8.2% of half hours the wind and solar total is below fuel wind, so it is not
  simply "fuel wind plus small farms".
* Against wind forecasts issued 1 to 6 h ahead (3,566 daytime hours): bias +1,071 MW
  against fuel wind and -392 MW against the wind and solar total, but correlation
  0.966 against fuel wind and 0.945 against the total.

Decision: the price model uses fuel wind (complete, cleaner signal). Wind and solar
actuals stay in Silver for solar and comparison. The model phase will test both.

### Forecasts as of 10:00
* Demand forecast age at decision: 0.05 to 1.25 h, mean 1.04 h.
* Wind forecast age at decision: always 0.5 h. The issue used is published at
  09:30 UK time, so issue times follow the UK clock, not UTC.
* Day ahead wind and solar is available by 10:00 only for periods 1 and 2,
  on 431 of 738 days. It is not a 10:00 input.
* Two day ahead issues are missing at the source (4 May and 22 Aug 2026),
  each leaving gaps across two target days.

### Weather
* When the normal 00:00 run is missing or has blank values, the downloader uses the
  newest complete earlier run (every 6 h, up to 48 h back). It falls back only for
  "run not available" or blank values, never for rate limits or server errors.
* 4 of 6 bad target days were rescued: 2025-08-06, 2025-08-07, 2025-08-10 and 2026-06-24.
  2025-08-08 and 2025-08-09 have no complete run within 48 h and stay as gaps.
* The hollow files for 2025-08-08 and 2026-06-24 were moved to a quarantine folder,
  on the laptop and in the Databricks volume, not deleted.
* Bronze reads the real run time from each file's metadata (`params.run`). Silver records
  `run_age_at_decision_hours`: 9 h on 430 days, 10 h on 308, 21 h on 1, 27 h on 2, 51 h on 1.
* Shortwave radiation is the mean over the previous hour; Gold must align it.

### Carbon intensity
* Settlement date and period derived from UTC time match Elexon's labels on all
  35,663 shared half hours, including the four clock change days.
* Mean 128.4 gCO2/kWh. A minimum of 0 and 43 null forecasts are flagged for Phase 5.

## Gold: one row per half hour, as known at 10:00 the day before

`workspace.gold.price_features` is built by `databricks/04_gold_build.ipynb`, which reads
Silver, builds everything in memory and saves Gold once. It replaces the exploratory
`04_gold` notebook, which rebuilt and saved Gold several times in sequence; running it
again would have put the old target back, so it was removed (it remains in git history).

35,712 rows (one per half hour of 744 target days), 45 columns.

* Target: `target_price` is the settled price, the money a battery is actually paid on,
  with `target_run_type`, `target_is_settled` and `target_is_final`. 0 missing.
  16,272 final, 19,440 settled but not final.
* `feed_price` is the market feed price, kept only to measure the effect of the switch.
  It is never a feature. 49 missing (all 48 of 2026-10-14, and 2026-10-13 period 48:
  the days after the feed was last downloaded).
* Forecasts: demand and wind as published by 10:00; weather from the run used for each
  day, with its age (9 or 10 h normally, up to 51 h after a fallback).
* Weather matching: snapshots (wind, cloud, temperature) use the hour nearest the middle
  of the half hour; shortwave radiation uses the hour after the half hour's hour starts.
  Checked: mean June radiation is 0 at night and peaks symmetrically around 13:00 UK.
* Recent actuals: a value is used only if it was published before the decision.
  Demand and wind: 2 days back (0 late rows measured). Prices: 3 and 7 days back, from
  the feed, because 20,937 prices are reissued up to a day late and 1,056 were
  republished in bulk months later (5 dates). 1,074 and 1,056 values are blanked by
  this rule. Daily price summaries need at least 43 of 48 prices in time, otherwise
  blank (3 days).
* Checks on the finished table: no decision after its half hour, no forecast from after
  the decision, no weather run fresher than the 6 h assumption. All 0.
* The target switch changed nothing else: compared with the previous Gold table (Delta
  time travel), all 38 non target columns are identical in all 35,712 half hours, and
  the old target equals `feed_price` everywhere.

Correlation with the target (signs match the market; all are weak), feed target against
settled target:

| Feature | Feed | Settled |
|---|---|---|
| fc_national_demand_mw | +0.265 | +0.273 |
| fc_wind_mw | −0.232 | −0.239 |
| w100_mean | −0.224 | −0.232 |
| rad_mean | −0.131 | −0.137 |
| temp_mean | −0.014 | −0.011 |
| price_lag3 | +0.158 | +0.167 |
| price_lag7 | +0.153 | +0.164 |
| price_d3_mean | +0.138 | +0.148 |
| demand_lag2 | +0.163 | +0.167 |
| wind_actual_lag2 | −0.024 | −0.028 |

Almost every relationship gets slightly stronger, consistent with the settled price
having fewer false values. This is a first look, not a model result; Phase 7 compares
both targets properly.

Note for Phase 7: "rows to predict" can no longer mean "rows with no target", because
the settled prices may be downloaded later than the features. It should mean the target
days after the decision being simulated.

## Open items
1. Done: empty replies and blank values are failures.
2. Done: weather falls back to the newest complete earlier run.
3. Done: Open-Meteo requests 3 forecast days instead of 7.
4. Done: wind forecast issue times documented as following UK time.
5. Phase 5: checks for complete days, empty files, zero values.
6. Phase 4: refresh Silver feed prices together with settled prices, so both cover the
   same days.
7. Tidy duplicate cells in `02_bronze` and `03_silver` (safe to run all, just slow).
8. Rename the `temp_temp_*` columns.
