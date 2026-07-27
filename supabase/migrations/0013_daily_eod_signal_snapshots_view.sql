-- Migration 0013: Daily EOD Signal Snapshots View
-- Returns the chronologically final (EOD) snapshot for each distinct session_date.
-- Used by EngineState.load() and OutlookGenerator to seed cross-session trailing histories
-- without pulling thousands of intraday 5-second tick rows from a single date.

CREATE OR REPLACE VIEW daily_eod_signal_snapshots AS
SELECT DISTINCT ON (session_date)
    session_date,
    ts,
    raw_readings,
    sub_scores,
    composite_score,
    market_state
FROM signal_snapshots
ORDER BY session_date DESC, ts DESC;
