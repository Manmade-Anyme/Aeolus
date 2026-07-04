-- TASK-014: ml_model_registry — versioned Isolation Forest + scaler + hysteresis
-- thresholds per config_type. RetentionJob prunes to the newest
-- registry_keep_versions per config_type (default 30); never below 1 (ADR-014).

create table if not exists ml_model_registry (
    id                      uuid primary key default gen_random_uuid(),
    config_type             config_type not null,
    version                 integer not null,
    feature_set_version     integer not null,
    model_blob              text not null,
    sklearn_version         text not null,
    scaler_mean             jsonb not null,
    scaler_std              jsonb not null,
    flag_threshold          double precision not null,
    clear_threshold         double precision not null,
    window_start            date not null,
    window_end              date not null,
    sample_count            integer not null,
    trading_day_count       integer not null,
    trained_at              timestamptz not null,
    created_at              timestamptz not null default now(),
    unique (config_type, version)
);

create index if not exists idx_ml_model_registry_config_version
    on ml_model_registry (config_type, version desc);
