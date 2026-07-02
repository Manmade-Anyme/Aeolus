-- TASK-001: daily_outlook — one row per trading day, the pre-market forecast.
-- trend_exhaustion_flag is its own column (not folded into contributing_inputs)
-- per Build Prompt 1 / docs/DATA_MODEL.md. realized_archetype is null until
-- backfilled after close by TASK-012.

create table if not exists daily_outlook (
    id                          uuid primary key default gen_random_uuid(),
    session_date                date not null unique,
    predicted_archetype         day_archetype not null,
    archetype_confidence        double precision not null,
    contributing_inputs         jsonb not null,
    trend_exhaustion_flag       boolean not null,
    straddle_level_vs_history   double precision not null,
    realized_archetype          day_archetype,
    created_at                  timestamptz not null default now()
);

create index if not exists idx_daily_outlook_session_date on daily_outlook (session_date);
