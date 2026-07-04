-- TASK-014: ml_feature_store — permanent training corpus for the ML anomaly
-- overlay. NEVER trimmed by RetentionJob (see jobs/retention.py PROTECTED_TABLES).
-- source_snapshot_id is a plain uuid, not a FK — trimming signal_snapshots must
-- never cascade into or block on this table (see ADR-014).

create table if not exists ml_feature_store (
    id                      uuid primary key default gen_random_uuid(),
    ts                      timestamptz not null,
    session_date            date not null,
    config_type             config_type not null,
    source_snapshot_id      uuid not null unique,
    feature_set_version     integer not null,
    raw_values              jsonb not null,
    standardized_values     jsonb,
    created_at              timestamptz not null default now()
);

create index if not exists idx_ml_feature_store_ts on ml_feature_store (ts);
create index if not exists idx_ml_feature_store_config_type on ml_feature_store (config_type);
