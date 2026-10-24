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

### System prices (market feed: features)

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
* Since Phase 3 this feed is used only for features (lagged and masked by publish time).
  The target is the settled price below, because the feed differs from settlement in
  about 4% of half hours and contains 30 false zeros

### Settled prices (target)

* Endpoint: `/market-reports/best-view-prices?startDate=D&endDate=D` (no `runType`)
* Source name: `elexon_settled_prices`. One request per settlement date.

The same half hourly imbalance price as `elexon_system_prices`, but taken from settlement.
Elexon recalculates each day in a series of settlement runs over roughly 14 months:
II (Interim Information), SF (Settlement Final), R1, R2, R3 and RF (Final Reconciliation),
plus DF after a dispute. Before the first run exists, the report falls back to the market
feed and labels the rows `BM`. With no `runType`, the report returns the latest run
available for each half hour, labelled with its run.

**Validation:** the same checks as system prices (right date, valid and unique periods,
not empty), plus: every run label must be one of BM, II, SF, R1, R2, R3, RF, DF, and both
prices must be numbers.

**Refetch:** settlement runs keep arriving for about 14 months, so on Sundays this source
looks back `long_refetch_days: 430` instead of the usual 3 days
(`config/settings.yaml`, `elexon.settled_prices`). Elexon keeps each run available for
months, so a weekly look back misses no run, only its exact arrival day. Every newer run
lands as a new file, which builds the revision history by run.

#### Why we added it: old versions of the feed are not kept

`/balancing/settlement/system-prices/{date}` returns only the latest version of each half
hour (for 2025-08-20, every row was created on 2025-12-12). `/datasets/DISEBSP` and
`/datasets/DISEBSP/stream` return 404. So the original publications of republished prices
cannot be recovered from the API.

The settlement runs can. Asking `best-view-prices` for each `runType` on 11 sample days
(October 2024 to October 2026) showed that Elexon keeps a moving window of runs: recent
days still have II and SF, older days only have their later runs (R3 and RF, or RF only).
Early runs were available from about April 2026 in that sample.

#### Findings: how much do prices change after publication?

**Run to run, 1 April to 20 October 2026** (`data/exploration/price_runs_2026.csv`):

| Step | Half hours compared | Changed | Over £1 | Over £10 | Typical change when changed | Biggest |
|---|---|---|---|---|---|---|
| Feed → II | 9,648 | 434 (4.5%) | 128 | 75 | £0.03 | £164.99 |
| II → SF | 8,832 | 177 (2.0%) | 16 | 11 | £0.02 | £122.00 |
| SF → R1 | 7,632 | 46 (0.6%) | 1 | 0 | £0.02 | £1.24 |
| R1 → R2 | 4,464 | 14 (0.3%) | 0 | 0 | £0.01 | £0.04 |

Almost all movement happens before SF (about a month). Most changes are pennies, but a few
are large. In this window, 83 half hours moved by more than £10 on 33 different days,
73 of them upwards.

**Feed vs settled price, 1 October 2024 to 20 October 2026**
(`data/exploration/price_vs_settled.csv`, latest run per half hour):

- 36,000 half hours compared; 1,527 differ (4.2%), so the feed matches the settled price
  in 95.8% of half hours
- 332 differ by more than £10: 241 up, 91 down. When the feed is off by a lot, it is
  usually too low
- Runs behind the settled prices: RF 16,272, R3 9,554, R2 4,894, R1 3,168, SF 1,200,
  II 816, BM 96 (19 and 20 October, before any settlement run)
- **Zeros:** the feed has 177 half hours at exactly £0.00. 147 are real (the settled price
  is also zero, for example 25 December periods 47 and 48). 30 are not: the settled price
  was up to £164.99. 12 of the 30 are on 3 to 5 October 2026; the rest are scattered back
  to December 2024. 3 settled zeros are not zero in the feed
- The same comparison inside the lakehouse (Silver, 35,663 half hours) gives 1,521
  changed, 241 up and 91 down over £10, and the same 177 / 147 / 30 zeros

#### Decision

- **Features keep the feed price**, exactly as published and masked by publish time,
  because that is what a trader sees at 10am. Correcting it with settlement data from
  weeks later would leak the future into the inputs
- **The target and the backtest use the settled price**, because that is what a battery
  is paid on. Gold keeps the feed price as `feed_price`, so the effect of the switch can
  be measured in Phase 7

#### Limitations

- Early runs (II, SF, R1) exist only for roughly the last six months, so revision sizes
  before that cannot be measured
- `best-view-prices` gives each run's label but not its publication time
- The newest days are II or BM and can still change

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
* Issued 8 times a day. Seen in October 2026 (UK summer time) at 03:30, 05:30, 08:30,
  10:30, 12:30, 16:30, 19:30, 23:30 UTC
* The issue used at the 10:00 decision follows the UK clock: across all 744 target days
  it was published exactly 0.5 h before the decision, so 08:30 UTC in summer and
  09:30 UTC in winter (09:30 UK time all year). The other issues probably follow the
  UK clock too, but that has not been checked
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
* Runs take about 4 to 6 hours to become available. **For day D we use the 00:00 UTC run on D-1**.
  If that run is missing or has blank values, the newest complete earlier run is used
  (every 6 h, up to 48 h back)
* Each run covers 168 hours
* Variables: `temperature_2m` (°C), `shortwave_radiation` (W/m², average over the previous hour),
  `wind_speed_100m` (km/h), `cloud_cover` (%)
* `shortwave_radiation` is empty in the first hour of a run (no previous hour)
* Several locations can be requested in one call; results come back as a list in request order,
  with coordinates snapped to the model grid
* 12 points: 5 offshore wind, 1 onshore wind, 3 solar, 3 demand (temperature).
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

**Open-Meteo, 2 days** (2025-08-08, 2025-08-09). The API says several ECMWF IFS
runs in early August 2025 are not available, and retrying does not help. After the
backfill, the weather fallback (newest complete earlier run, up to 48 h back) rescued
2025-08-06, 2025-08-07, 2025-08-10 and 2026-06-24. For 2025-08-08 and 2025-08-09 no
complete run exists within 48 h, so they stay as gaps. Models must handle missing
weather days.

**Day ahead wind and solar, 2 days** (2026-04-26, 2026-04-27). Elexon returned
every record twice with the same publishTime (2026-04-25T16:45:04Z) but
different values. For example, on 2026-04-26 period 10, Wind Onshore was 438.4
in one copy and 2148.0 in the other. Nothing in the data shows which copy is
correct, so these days are rejected rather than guessed. This dataset is
comparison only, never a model input, so the impact is small.
