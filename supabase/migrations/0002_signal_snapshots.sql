-- TASK-001: signal_snapshots — one row per computation cycle, the ML feature set
-- by construction. raw_readings/sub_scores are jsonb because the category roster
-- grows across TASK-003..007; composite must be recomputable retroactively from
-- raw_readings alone if weights change (docs/DATA_MODEL.md, CONSTRAINTS.md).

create table if not exists signal_snapshots (
    id                uuid primary key default gen_random_uuid(),
    ts                timestamptz not null,
    session_date      date not null,
    config_type       config_type not null,
    dte               integer not null,
    raw_readings      jsonb not null,   -- {category: {raw_value, reference_band}}
    sub_scores        jsonb not null,   -- {category: sub_score}
    composite_score   double precision not null,
    market_state      market_state not null,
    system_status     system_status not null,
    reasons           jsonb not null,   -- {category: reason_string}
    created_at        timestamptz not null default now()
);

create index if not exists idx_signal_snapshots_ts on signal_snapshots (ts);
create index if not exists idx_signal_snapshots_config_type on signal_snapshots (config_type);
