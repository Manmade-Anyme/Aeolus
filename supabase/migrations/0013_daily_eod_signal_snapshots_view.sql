-- Migration 0013: Daily EOD Signal Snapshots View
-- Returns the chronologically final (EOD) snapshot for each distinct session_date.
-- Used by EngineState.load() and OutlookGenerator to seed cross-session trailing histories
-- without pulling thousands of intraday 5-second tick rows from a single date.
--
-- Why this exists: the engine writes one signal_snapshots row per 5s cycle
-- (~4,500 rows/session), so any bounded `.limit()` over the raw table returns
-- rows from one session_date only. Every cross-session trailing history then
-- collapsed to a single element, and _percentile_rank over a 1-element sample
-- can only return 0.0 or 1.0 -- which is why gex_regime,
-- cvd_direction_and_divergence and delta_imbalance_and_absorption sat locked
-- at sub_score 1.00 during live sessions.
--
-- Idempotent: safe to re-run against a database that already applied an
-- earlier revision of this migration.

-- Supports the view's DISTINCT ON. Without it, every EngineState.load() and
-- every outlook run sorts the whole signal_snapshots table (~4,500 rows/session
-- x 90-day retention). Column order matches the view's ORDER BY so Postgres can
-- walk the index instead of materialising and sorting the full relation.
CREATE INDEX IF NOT EXISTS idx_signal_snapshots_session_date_ts
    ON signal_snapshots (session_date DESC, ts DESC);

-- security_invoker = on evaluates the view under the querying role rather than
-- the view owner. No RLS policies exist on signal_snapshots today so this is
-- operationally inert, but it stops the view from silently becoming an RLS
-- bypass if a policy is added later, and clears Supabase's database linter
-- `security_definer_view` error.
CREATE OR REPLACE VIEW daily_eod_signal_snapshots
WITH (security_invoker = on) AS
SELECT DISTINCT ON (session_date)
    session_date,
    ts,
    raw_readings,
    sub_scores,
    composite_score,
    market_state
FROM signal_snapshots
ORDER BY session_date DESC, ts DESC;
