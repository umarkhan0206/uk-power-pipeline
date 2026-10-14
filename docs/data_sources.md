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

* Endpoints: `/forecast/demand/day-ahead/history?publishTime=&boundary=N` (used),
  `/evolution` and `/earliest` (used only during exploration)
* Two kinds of issue exist:
  * a **long issue**, seen once a day at 07:45 UTC, reaching about two days ahead
  * **short updates** about every 30 minutes, reaching only to about 03:30 UTC on the next day
* For one half hour (18:00 UTC on 5 Oct 2026), `/evolution` listed **61 issues**.
  The `/earliest` view showed only the first of them
* Publish times wander by a minute or two, and issues are sometimes missed
  (no issue at about 07:47 on 5 Oct)
* `/history` returns the latest issue at or before `publishTime`. As of 09:00 UTC on 4 Oct
  it returned a short update from 08:46 covering only the first 10 periods of 5 Oct
* **Neither obvious approach is right:**
  * "earliest" uses an issue from two days before for the first periods of the day
  * a single "as of decision time" request misses most of the target day
* **Approach used:** request the forecast as of the decision time, then step back to one second
  before that issue's publish time, repeating until every period of the target day is covered
  (limit 12 steps). Each period then gets the most recent forecast published before the decision.
  For 5 Oct 2026 this took 3 requests: periods 1 to 10 from the 08:46 update,
  periods 11 to 48 from the 07:45 long issue
* Every response is checked: one issue per response, published no later than the time asked for
  (otherwise it is rejected as leakage), boundary `N`, no duplicate start times
* Every response fetched is landed, including ones that add no new periods.
  Choosing the forecast for each period is repeated in Silver from the landed files
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

## Backfill results and known gaps

The full backfill covered 2024-10-01 to 2026-10-13 and took three runs.

| Run | Time | Landed | Failed | What happened |
|---|---|---|---|---|
| 1 | 7,781 s | 6,523 | 139 | Full backfill |
| 2 | 103 s | 72 | 67 | Open-Meteo rate limited days retried |
| 3 | 34 s | 61 | 6 | After the revision fix below |

Final day counts on disk: 743 for each actuals source, 744 for the demand and
wind forecasts (they include tomorrow), 741 for day ahead wind and solar, 740
for Open-Meteo.

### Open-Meteo rate limiting (resolved)

In run 1, 72 consecutive days (2026-06-27 to 2026-09-06) failed with HTTP 429.
Our retries wait at most about a minute, which is not enough for an hourly
quota. All 72 landed on a rerun. A slower request rate for Open-Meteo would
avoid this in future backfills.

### Wind and solar revisions (resolved)

57 days of wind and solar actuals and 4 days of day ahead wind and solar failed
the duplicate check. Elexon had republished some periods later with a new
publishTime. Example: on 2025-09-16, period 18 was published at 09:58 UTC and
again at 14:10 UTC; wind was unchanged and solar went from 3264 to 3267 MW.
These are revisions, not errors, so the duplicate key for both sources is now
(period, psrType, publishTime). Both versions are kept in the landing zone;
Silver keeps the latest one.

### Known permanent gaps

**Open-Meteo, 4 days** (2025-08-06, 2025-08-07, 2025-08-09, 2025-08-10).
The API says the ECMWF IFS 00:00 runs for 2025-08-05, 06, 08 and 09 are not
available. Retrying does not help. Models must handle missing weather days.

**Day ahead wind and solar, 2 days** (2026-04-26, 2026-04-27). Elexon returned
every record twice with the same publishTime (2026-04-25T16:45:04Z) but
different values. For example, on 2026-04-26 period 10, Wind Onshore was 438.4
in one copy and 2148.0 in the other. Nothing in the data shows which copy is
correct, so these days are rejected rather than guessed. This dataset is
comparison only, never a model input, so the impact is small.