-- TASK-001: enum types.
-- market_state and system_status are separate types by design — a value of one
-- can never be assigned to a column typed as the other. See docs/CONSTRAINTS.md.
-- Postgres has no CREATE TYPE IF NOT EXISTS, so guard via DO block for idempotent re-runs.

do $$ begin
    create type market_state as enum ('NO_GO', 'PREPARE', 'GO');
exception
    when duplicate_object then null;
end $$;

do $$ begin
    create type system_status as enum ('OK', 'STALE', 'DISCONNECTED');
exception
    when duplicate_object then null;
end $$;

do $$ begin
    create type config_type as enum ('EXPIRY', 'NON_EXPIRY');
exception
    when duplicate_object then null;
end $$;

do $$ begin
    create type day_archetype as enum (
        'clean_trend',
        'grinding_trend',
        'pinned_range',
        'choppy_range',
        'breakout_transition',
        'event_gap',
        'double_distribution'
    );
exception
    when duplicate_object then null;
end $$;

do $$ begin
    create type outcome_direction as enum ('UP', 'DOWN', 'FLAT');
exception
    when duplicate_object then null;
end $$;
