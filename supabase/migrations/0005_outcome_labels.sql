-- TASK-001: outcome_labels — backfilled enrichment, never written live (labels
-- don't exist at signal-time). Written by TASK-012 only, joining forward market
-- data back onto historical snapshots/transitions at +15/+30/+60 min.
--
-- FKs use ON DELETE SET NULL, not CASCADE: this is an append-only audit log,
-- nothing should delete rows, but an accidental snapshot delete must not
-- silently destroy label data too.
--
-- NOTE: outcome_labels_has_source below is dropped by 0006 — it conflicts
-- with ON DELETE SET NULL (see 0006 for why). Kept here unmodified since this
-- file already ran against the live project; don't edit applied migrations.

create table if not exists outcome_labels (
    id                      uuid primary key default gen_random_uuid(),
    snapshot_id             uuid references signal_snapshots (id) on delete set null,
    transition_id           uuid references state_transitions (id) on delete set null,
    horizon_minutes         smallint not null check (horizon_minutes in (15, 30, 60)),
    straddle_price_change   double precision not null,
    realized_move           double precision not null,
    direction               outcome_direction not null,
    created_at              timestamptz not null default now(),
    constraint outcome_labels_has_source check (
        snapshot_id is not null or transition_id is not null
    )
);

create index if not exists idx_outcome_labels_snapshot_id on outcome_labels (snapshot_id);
create index if not exists idx_outcome_labels_transition_id on outcome_labels (transition_id);
