-- AEOLUS — Complete Consolidated Supabase Database Schema
-- Includes all migrations (0001..0013) and the shared api_keys table.
-- Can be executed all at once in the Supabase SQL Editor.

-- ============================================================================
-- 1. ENUM TYPES
-- ============================================================================

DO $$ BEGIN
    CREATE TYPE market_state AS ENUM ('NO_GO', 'PREPARE', 'GO');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE system_status AS ENUM ('OK', 'STALE', 'DISCONNECTED');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE config_type AS ENUM ('EXPIRY', 'NON_EXPIRY');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE day_archetype AS ENUM (
        'clean_trend',
        'grinding_trend',
        'pinned_range',
        'choppy_range',
        'breakout_transition',
        'event_gap',
        'double_distribution'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE outcome_direction AS ENUM ('UP', 'DOWN', 'FLAT');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ============================================================================
-- 2. CORE ENGINE TABLES
-- ============================================================================

-- Shared api_keys table (stores Dhan client credentials)
CREATE TABLE IF NOT EXISTS api_keys (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider     TEXT NOT NULL,
    client_id    TEXT NOT NULL,
    access_token TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- signal_snapshots — one row per computation cycle (ML feature set)
CREATE TABLE IF NOT EXISTS signal_snapshots (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts                TIMESTAMPTZ NOT NULL,
    session_date      DATE NOT NULL,
    config_type       config_type NOT NULL,
    dte               INTEGER NOT NULL,
    raw_readings      JSONB NOT NULL,   -- {category: {raw_value, reference_band}}
    sub_scores        JSONB NOT NULL,   -- {category: sub_score}
    composite_score   DOUBLE PRECISION NOT NULL,
    market_state      market_state NOT NULL,
    system_status     system_status NOT NULL,
    reasons           JSONB NOT NULL,   -- {category: reason_string}
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_signal_snapshots_ts ON signal_snapshots (ts);
-- Supports daily_eod_signal_snapshots' DISTINCT ON (see VIEWS below); column order matches its ORDER BY.
CREATE INDEX IF NOT EXISTS idx_signal_snapshots_session_date_ts
    ON signal_snapshots (session_date DESC, ts DESC);
CREATE INDEX IF NOT EXISTS idx_signal_snapshots_config_type ON signal_snapshots (config_type);

-- state_transitions — log of confirmed, debounced state changes
CREATE TABLE IF NOT EXISTS state_transitions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts                  TIMESTAMPTZ NOT NULL,
    from_state          market_state NOT NULL,
    to_state            market_state NOT NULL,
    trigger_categories  JSONB NOT NULL,  -- list[str]
    reason              TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_state_transitions_ts ON state_transitions (ts);

-- daily_outlook — pre-market forecast per trading day
CREATE TABLE IF NOT EXISTS daily_outlook (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_date                DATE NOT NULL UNIQUE,
    predicted_archetype         day_archetype NOT NULL,
    archetype_confidence        DOUBLE PRECISION NOT NULL,
    contributing_inputs         JSONB NOT NULL,
    trend_exhaustion_flag       BOOLEAN NOT NULL,
    straddle_level_vs_history   DOUBLE PRECISION NOT NULL,
    realized_archetype          day_archetype,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_daily_outlook_session_date ON daily_outlook (session_date);

-- outcome_labels — backfilled enrichment data
CREATE TABLE IF NOT EXISTS outcome_labels (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id             UUID REFERENCES signal_snapshots (id) ON DELETE SET NULL,
    transition_id           UUID REFERENCES state_transitions (id) ON DELETE SET NULL,
    horizon_minutes         SMALLINT NOT NULL CHECK (horizon_minutes IN (15, 30, 60)),
    straddle_price_change   DOUBLE PRECISION NOT NULL,
    realized_move           DOUBLE PRECISION NOT NULL,
    direction               outcome_direction NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_outcome_labels_snapshot_horizon UNIQUE (snapshot_id, horizon_minutes),
    CONSTRAINT uq_outcome_labels_transition_horizon UNIQUE (transition_id, horizon_minutes)
);

CREATE INDEX IF NOT EXISTS idx_outcome_labels_snapshot_id ON outcome_labels (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_outcome_labels_transition_id ON outcome_labels (transition_id);

-- scrip_master_cache — fallback cache for Dhan scrip master subset (TASK-002 hotfix)
CREATE TABLE IF NOT EXISTS scrip_master_cache (
    id          INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    csv_data    TEXT NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 3. ML ANOMALY OVERLAY TABLES
-- ============================================================================

-- ml_feature_store — permanent training corpus for ML overlay
CREATE TABLE IF NOT EXISTS ml_feature_store (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts                      TIMESTAMPTZ NOT NULL,
    session_date            DATE NOT NULL,
    config_type             config_type NOT NULL,
    source_snapshot_id      UUID NOT NULL UNIQUE,
    feature_set_version     INTEGER NOT NULL,
    raw_values              JSONB NOT NULL,
    standardized_values     JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ml_feature_store_ts ON ml_feature_store (ts);
CREATE INDEX IF NOT EXISTS idx_ml_feature_store_config_type ON ml_feature_store (config_type);

-- ml_model_registry — versioned Isolation Forest models
CREATE TABLE IF NOT EXISTS ml_model_registry (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_type             config_type NOT NULL,
    version                 INTEGER NOT NULL,
    feature_set_version     INTEGER NOT NULL,
    model_blob              TEXT NOT NULL,
    sklearn_version         TEXT NOT NULL,
    scaler_mean             JSONB NOT NULL,
    scaler_std              JSONB NOT NULL,
    flag_threshold          DOUBLE PRECISION NOT NULL,
    clear_threshold         DOUBLE PRECISION NOT NULL,
    window_start            DATE NOT NULL,
    window_end              DATE NOT NULL,
    sample_count            INTEGER NOT NULL,
    trading_day_count       INTEGER NOT NULL,
    trained_at              TIMESTAMPTZ NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (config_type, version)
);

CREATE INDEX IF NOT EXISTS idx_ml_model_registry_config_version
    on ml_model_registry (config_type, version DESC);

-- ml_anomaly_scores — advisory-only live scoring log
CREATE TABLE IF NOT EXISTS ml_anomaly_scores (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts                      TIMESTAMPTZ NOT NULL,
    session_date            DATE NOT NULL,
    config_type             config_type NOT NULL,
    source_snapshot_id      UUID NOT NULL,
    score                   DOUBLE PRECISION NOT NULL,
    flagged                 BOOLEAN NOT NULL,
    ml_status               TEXT NOT NULL CHECK (ml_status IN ('ACTIVE', 'WARMING_UP')),
    top_features            JSONB,
    model_version_id        UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ml_anomaly_scores_ts ON ml_anomaly_scores (ts);
CREATE INDEX IF NOT EXISTS idx_ml_anomaly_scores_config_type ON ml_anomaly_scores (config_type);

-- ============================================================================
-- 4. VIEWS
-- ============================================================================

-- daily_eod_signal_snapshots — returns the chronologically final (EOD) snapshot for each distinct session_date.
-- Cross-session trailing histories are seeded from this, never from signal_snapshots directly: the engine writes
-- one row per 5s cycle, so a bounded .limit() over the raw table only ever covers a single session_date.
-- security_invoker = on keeps the view from becoming an RLS bypass if a policy is later added to signal_snapshots.
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
