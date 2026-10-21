# Lakehouse (Databricks Free Edition)

## Layout

* Catalog `workspace`, schemas `bronze`, `silver`, `gold` (see `databricks/00_setup.sql`)
* Volume `workspace.bronze.landing` holds the landing zone files
* Notebooks run in order: `00_setup.sql`, `01_unpack_landing`, `02_bronze`, `03_silver`

## Moving the landing zone

16,346 files (293 MB) were zipped to 27.6 MB, uploaded to the volume and unpacked.
Windows zip paths use backslashes, so paths were normalised while unpacking.
`bronze.landing_files` recomputes the SHA256 of all 8,173 data files and compares it
with the fingerprint in each file name: 0 mismatches, so every file arrived byte for byte.

## Bronze: data as received

| Table | Rows | Notes |
|---|---|---|
| elexon_system_prices | 35,663 | 743 days |
| elexon_demand_outturn | 35,664 | |
| elexon_generation_by_fuel | 713,280 | 20 fuel types per half hour |
| elexon_wind_solar_actual | 108,963 | includes republished versions |
| elexon_wind_solar_day_ahead | 106,416 | 738 days with data |
| elexon_demand_forecast | 93,109 | 2,233 files (several issues per day) |
| elexon_wind_forecast | 53,928 | hourly |
| carbon_intensity | 36,407 | one extra window per file |
| open_meteo_ecmwf | 742 files | 12 locations; 72 to 168 hours per file; real run time from metadata |
| landing_files | 8,173 | one row per data file, including empty ones |

Every row keeps `_source_file`, `_partition_day`, `_fetched_at` and `_ingested_at`.

Found in Bronze:
* 13 Oct 2026 system prices has 47 of 48 periods: the backfill fetched it at 23:00 UTC,
  before the last price was published. The incremental refetch heals this.
* 3 day ahead files are empty (`"data": []`): 2025-02-09, 2026-01-09, 2026-03-05.
  `explode` drops them silently, which is why `landing_files` exists.

## Silver: typed, one row per record, point in time correct

Measured data keeps the latest version per key. Forecasts keep the latest version
published at or before the decision time (10:00 UK on the day before the target day).

| Table | Rows | Key checks |
|---|---|---|
| system_prices | 35,663 | unique per half hour |
| demand_outturn | 35,664 | 0 incomplete days |
| generation_by_fuel | 713,280 | 0 incomplete days |
| wind_solar_actual | 104,652 | 780 half hours missing at source |
| carbon_intensity | 35,664 | 0 label mismatches with Elexon |
| demand_forecast_asof | 35,712 | 0 rows published after 10:00 |
| wind_forecast_asof | 17,856 | 0 rows published after 10:00 |
| weather_forecast_asof | 213,696 | run plus 6 h before 10:00 on every row, 0 blank values |
| wind_solar_day_ahead_asof | 105,984 | as known at 19:00, comparison only |

## Gold: one row per half hour, as known at 10:00 the day before

`workspace.gold.price_features`: 35,712 rows (744 target days), 41 columns.
35,663 rows have a target price; 49 do not (all 48 of 2026-10-14, and 2026-10-13 period 48).

* Target: the latest system price (the final settlement truth).
* Forecasts: demand and wind as published by 10:00; weather from the run used for each
  day, with its age (9 or 10 h normally, up to 51 h after a fallback).
* Weather matching: snapshots (wind, cloud, temperature) use the hour nearest the middle
  of the half hour; shortwave radiation uses the hour after the half hour's hour starts.
  Checked: mean June radiation is 0 at night and peaks symmetrically around 13:00 UK.
* Recent actuals: a value is used only if it was published before the decision.
  Demand and wind: 2 days back (0 late rows measured). Prices: 3 and 7 days back,
  because 20,937 prices are reissued up to a day late and 1,056 were republished in
  bulk months later (5 dates). 1,074 and 1,056 values are blanked by this rule.
  Daily price summaries need at least 43 of 48 prices in time, otherwise blank (3 days).
* Checks on the finished table: no decision after its half hour, no forecast from after
  the decision, no weather run fresher than the 6 h assumption.
* Correlation with the target price (signs match the market; all are weak):
  demand forecast +0.265, wind forecast -0.232, mean wind speed -0.224,
  radiation -0.131, price 3 days back +0.158.

### System prices
* Sell and buy prices are identical in all 35,663 half hours (single imbalance price).
* Range -£97.92 to £2,900 per MWh. Median £91.50, mean £90.45.
* 1,950 negative half hours, mostly around midday on weekends and holidays.
* 29 half hours above £500. All eight half hours at £2,900 are on 8 Jan 2025,
  15:00 to 19:00, with a short system (positive net imbalance volume).
  The backtest must report results with and without this day.

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

## Open items
1. Done: empty replies and blank values are failures.
2. Done: weather falls back to the newest complete earlier run.
3. Done: Open-Meteo requests 3 forecast days instead of 7.
4. Done: wind forecast issue times documented as following UK time.
5. Phase 5: checks for complete days, empty files, zero values.