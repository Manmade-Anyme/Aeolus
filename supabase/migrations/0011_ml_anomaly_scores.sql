-- TASK-014: ml_anomaly_scores — advisory-only live scoring log. Age-trimmed by
-- RetentionJob (default 90 days), unlike ml_feature_store which is permanent.
-- No FK to signal_snapshots — same rationale as ml_feature_store (ADR-014).

create table if not exists ml_anomaly_scores (
    id                      uuid primary key default gen_random_uuid(),
    ts                      timestamptz not null,
    session_date            date not null,
    config_type             config_type not null,
    source_snapshot_id      uuid not null,
    score                   double precision not null,
    flagged                 boolean not null,
    ml_status               text not null check (ml_status in ('ACTIVE', 'WARMING_UP')),
    top_features            jsonb,
    model_version_id        uuid,
    created_at              timestamptz not null default now()
);

create index if not exists idx_ml_anomaly_scores_ts on ml_anomaly_scores (ts);
create index if not exists idx_ml_anomaly_scores_config_type on ml_anomaly_scores (config_type);
