# Data sources

What each source provides, how it behaves, and how the pipeline uses it.
Every observation below comes from probing the live APIs in
`notebooks/01_explore_apis.ipynb` (October 2026). Where something was only
seen on one day, it says so.

## Decision time and the leakage rule

The forecaster predicts every half hour of UK day D, deciding at
**10:00 UK time on day D-1** (a config setting).

A value may be used as a model feature for day D only if it was
**published before the decision time**. Where a source does not tell us
when a value was published, it is not used as a forecast feature.
The pipeline checks this rule in code rather than assuming it.

## Elexon Insights API

Base URL: `https://data.elexon.co.uk/bmrs/api/v1`. No API key needed.

### System prices (target)

* Endpoint: `/balancing/settlement/system-prices/{settlementDate}`
* Grain: one row per settlement period (48 on a normal day, 46 or 50 on clock change days)
* `settlementDate` and `settlementPeriod` are in UK local time; `startTime` is UTC.
  Period 1 of 5 Oct 2026 started at `2026-10-04T23:00:00Z` (BST)
* `systemSellPrice` and `systemBuyPrice` were identical on every period checked
  (GB uses a single imbalance price)
* **Republishing:** each record is reissued about 24.7 hours after the period starts.
  A single fetch can contain a mix of first and second versions.
  On 6 Oct 2026, 38 reissued records were compared with their first versions:
  all 38 were identical in every field except `createdDateTime`
* Consequence: a new `createdDateTime` does not mean the data changed.
  Revisions are detected by comparing values (a hash of the record), and every
  fetch is stored without overwriting
* Consequence: incremental runs re-fetch at least the last two days
* Limitation: first versions are only captured from the day the pipeline starts.
  A historical backfill only sees later versions

### Generation by fuel type (FUELHH)

* Endpoint: `/datasets/FUELHH?settlementDateFrom=&settlementDateTo=`
* Long format: one row per fuel type per period (20 fuel types, 960 rows per day)
* Includes interconnectors (`INT...`), pumped storage (`PS`), gas (`CCGT`, `OCGT`)
* **No solar, and wind is incomplete:** only transmission connected generation.
  Do not use this alone for renewables
* Records were returned in reverse period order: never rely on API ordering
* Use: recent history features only

### Demand outturn (INDO, ITSDO)

* Endpoint: `/demand/outturn?settlementDateFrom=&settlementDateTo=`
* Wide format: `initialDemandOutturn` and `initialTransmissionSystemDemandOutturn` per period
* Published at the end of each period
* "Initial" figures: may be refined later in other datasets
* Use: recent history features only

### Wind and solar actuals (AGWS / B1630)

* Endpoint: `/generation/actual/per-type/wind-and-solar?from=&to=&settlementPeriodFrom=1&settlementPeriodTo=50`
  (adding the period parameters makes `from` and `to` act as settlement dates)
* Long format: Solar, Wind Offshore, Wind Onshore (144 rows per day)
* Published about 2 hours after the period ends
* Officially "actual or estimated": solar is partly estimated
* Use: recent history features only

### Day ahead demand forecast (NDF, TSDF)

* Endpoint: `/forecast/demand/day-ahead/earliest?from=&to=`
* `from` and `to` are UTC start times and **both ends are inclusive**
  (one day request returned 49 rows). Deduplicate on `startTime`
* Seen issued once a day at 07:45 UTC, covering about 20 to 44 hours ahead
* Every value for day D in the "earliest" view was published before 09:00 UTC on D-1
* To do: check `/evolution` for issues later in the day that "earliest" would hide
* Use: model feature

### Wind forecast (WINDFOR)

* Endpoints: `/forecast/generation/wind/history?publishTime=` and `/evolution?startTime=`
* **Hourly**, not half hourly
* Issued 8 times a day at 03:30, 05:30, 08:30, 10:30, 12:30, 16:30, 19:30, 23:30 UTC
* Each issue covers about 3 days
* `/history` returns the latest issue at or before the requested `publishTime`
  (asking for 09:00 returned the 08:30 issue), which makes the as of join simple
* The "earliest" view is leak free but stale (41.5 to 64.5 hours old), so it is not used
* For the 18:00 UTC hour of 5 Oct, forecasts across 24 issues ranged from 10,387 to 11,503 MW
* Use: model feature, fetched as of the decision time. Code asserts `publishTime <= decision time`

### Day ahead wind and solar forecast (DGWS / B1440)

* Endpoint: `/forecast/generation/wind-and-solar/day-ahead?from=&to=&settlementPeriodFrom=&settlementPeriodTo=&processType=`
* `processType` is required: `day ahead`, `intraday process`, `intraday total`, or `all`
* On the day checked: day ahead issued 16:45 UTC on D-1, intraday at 07:00 and 11:00 UTC on D
* **All of these are after the 10:00 decision time**, so none can be a model feature
* Kept in the pipeline for comparison, and for a later experiment on an evening decision time

## Open-Meteo (weather forecasts)

* Endpoint: `https://single-runs-api.open-meteo.com/v1/forecast`, model `ecmwf_ifs`
* Request by run start time, e.g. `run=2026-10-04T00:00`
* ECMWF IFS runs are archived from 14 March 2024 (older runs are described as hindcasts).
  Most other models only from 2 April 2026
* ECMWF upgraded its model in May 2026, so forecast quality may shift within the data
* Runs take about 4 to 6 hours to become available. **For day D we use the 00:00 UTC run on D-1**
* Each run covers 168 hours
* Variables: `temperature_2m` (°C), `shortwave_radiation` (W/m², average over the previous hour),
  `wind_speed_100m` (km/h), `cloud_cover` (%)
* `shortwave_radiation` is empty in the first hour of a run (no previous hour)
* Several locations can be requested in one call; results come back as a list in request order,
  with coordinates snapped to the model grid
* 11 points: 5 offshore wind, 1 onshore wind, 3 solar, 3 demand (temperature).
  This is a simplification; weighting by installed capacity would be better
* Free API for non commercial use

## Carbon Intensity API (National Grid ESO)

* Endpoint: `https://api.carbonintensity.org.uk/intensity/{from}/{to}`
* Half hourly windows with `forecast`, `actual` and `index`
* Returns any window touching `from` (a one day request returned 49 rows, starting 30 minutes early).
  Filter on window start
* **No publish time**, so the forecast cannot be proven to predate the decision time
* Use: actuals as recent history features and on the dashboard. Forecast not used

## Rules that apply to every source

* Never trust API ordering or request boundaries: deduplicate and filter on our side
* Store every raw response unchanged, with fetch time
* Work in UTC internally; keep settlement date and period as labels
* Check the HTTP status before reading the body, and log the API's error message on failure