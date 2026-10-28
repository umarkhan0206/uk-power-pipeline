# UK Electricity Market Pipeline
[![tests](https://github.com/umarkhan0206/uk-power-pipeline/actions/workflows/tests.yml/badge.svg)](https://github.com/umarkhan0206/uk-power-pipeline/actions/workflows/tests.yml)

A pipeline that collects Great Britain electricity market data, forecasts next day prices,
and tests whether a grid battery could profit from those forecasts.

**Status: work in progress.** Phase 1 (ingestion) is being built now.
Everything described below as done is implemented and tested; everything else is planned.

## What it will do

1. **Ingest** half hourly market data from public APIs, incrementally, with a two year backfill
2. **Store** it in a lakehouse (Databricks, Delta Lake) as Bronze, Silver and Gold layers
3. **Track revisions** when published data is later changed
4. **Orchestrate** runs with Airflow, with data quality checks and alerts
5. **Forecast** next day prices with prediction intervals, validated walk forward
6. **Backtest** a battery trading against those forecasts
7. **Show** it all on a live dashboard

## Done so far

* Exploration of every data source, written up in [`docs/data_sources.md`](docs/data_sources.md)
* Validated settings, UTC logging, and an HTTP client with timeouts, retries, backoff and rate limiting
* An append only landing zone: raw responses stored byte for byte, never overwritten
* Source adapters for system prices, demand, generation by fuel, wind and solar actuals,
  and the day ahead demand forecast
* 106 tests, including real API responses from the day the clocks went back (50 half hours)

## Findings so far

* **System prices are reissued about 24.7 hours after first publication.** On the day checked,
  all 38 reissued records were identical, so the pipeline detects real revisions by comparing
  values, not timestamps.
* **The demand forecast has two kinds of issue.** For one half hour there were 61 versions.
  Asking for "the forecast at 10am" covered only 10 of the next day's 48 half hours, and the
  "earliest" view used a forecast 25 hours old for some of them. The pipeline steps back through
  issues until every half hour is covered by the latest forecast published before the decision.
* **The official day ahead solar forecast is published after a 10am decision**, so it cannot be
  a model input without leakage. Archived weather forecasts (Open-Meteo, ECMWF) are used instead.

## Key design rule

Forecasts for day D are made at **10:00 UK time on day D-1**. A value is only used as a model
input if it was published before that moment. The code checks this and rejects data that breaks it.

## Data sources

* [Elexon Insights API](https://data.elexon.co.uk/bmrs/api/v1): prices, demand, generation, forecasts
* [Open-Meteo](https://open-meteo.com/) Single Runs API: archived ECMWF weather forecasts (non commercial use)
* [National Grid ESO Carbon Intensity API](https://api.carbonintensity.org.uk)

## Running it

Requires Python 3.11.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

Settings live in [`config/settings.yaml`](config/settings.yaml).

## Project layout

```
config/          settings
docs/            data source notes and design decisions
notebooks/       API exploration
src/ukpower/     the package: config, logging, time helpers, ingestion
tests/           unit tests and real API fixtures
```