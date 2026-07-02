-- TASK-001: state_transitions — thin log, only rows where market_state actually
-- changed on a confirmed, debounced flip (never every cycle). This is what gets
-- posted to Discord (TASK-011).

create table if not exists state_transitions (
    id                  uuid primary key default gen_random_uuid(),
    ts                  timestamptz not null,
    from_state          market_state not null,
    to_state            market_state not null,
    trigger_categories  jsonb not null,  -- list[str]
    reason              text not null,
    created_at          timestamptz not null default now()
);

create index if not exists idx_state_transitions_ts on state_transitions (ts);
