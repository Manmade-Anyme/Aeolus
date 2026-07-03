-- TASK-012 fix: 0007's partial unique indexes (WHERE snapshot_id/transition_id
-- is not null) don't satisfy PostgREST's ON CONFLICT inference -- Postgres
-- only matches a partial index for ON CONFLICT (columns) if the same WHERE
-- predicate is repeated in the conflict clause itself, which PostgREST's
-- upsert doesn't do (it only ever emits ON CONFLICT (columns) with no WHERE).
-- Confirmed live: "no unique or exclusion constraint matching the ON CONFLICT
-- specification" (42P10) when OutcomeBackfillJob tried to upsert, 2026-07-03.
--
-- Fix: a plain (non-partial) UNIQUE constraint works instead, and needs no
-- partial predicate at all -- standard SQL/Postgres semantics already treat
-- NULL <> NULL for uniqueness purposes, so unique (snapshot_id, horizon_minutes)
-- silently ignores every transition-only row (snapshot_id IS NULL) without a
-- WHERE clause, while still enforcing true uniqueness among snapshot-sourced
-- rows. Same effect as 0007 intended, achieved with a form PostgREST can
-- actually target.
--
-- Don't edit 0007 (already applied) -- same precedent as 0006 fixing 0005.

drop index if exists uq_outcome_labels_snapshot_horizon;
drop index if exists uq_outcome_labels_transition_horizon;

alter table outcome_labels
    add constraint uq_outcome_labels_snapshot_horizon unique (snapshot_id, horizon_minutes);

alter table outcome_labels
    add constraint uq_outcome_labels_transition_horizon unique (transition_id, horizon_minutes);
