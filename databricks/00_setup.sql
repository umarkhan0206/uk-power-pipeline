-- One time setup for the lakehouse. Safe to run again (IF NOT EXISTS).
CREATE SCHEMA IF NOT EXISTS workspace.bronze
  COMMENT 'Raw data loaded as received';
CREATE SCHEMA IF NOT EXISTS workspace.silver
  COMMENT 'Cleaned, typed, one row per record, latest revision';
CREATE SCHEMA IF NOT EXISTS workspace.gold
  COMMENT 'Model and backtest ready tables';
CREATE VOLUME IF NOT EXISTS workspace.bronze.landing
  COMMENT 'Landing zone files uploaded from the ingestion pipeline';