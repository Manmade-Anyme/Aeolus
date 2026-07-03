-- TASK-012: partial unique indexes so OutcomeBackfillJob can upsert instead
-- of doing a race-prone application-level "select existing, skip if present"
-- check. A row has exactly one of snapshot_id/transition_id populated
-- (outcome_labels_has_source, enforced at the pydantic layer since 0006), so
-- two partial indexes are needed rather than one composite unique -- a plain
-- composite unique across both columns would allow duplicate
-- (snapshot_id, horizon_minutes) pairs as long as transition_id differed,
-- which isn't the guarantee this needs.

create unique index if not exists uq_outcome_labels_snapshot_horizon
    on outcome_labels (snapshot_id, horizon_minutes) where snapshot_id is not null;

create unique index if not exists uq_outcome_labels_transition_horizon
    on outcome_labels (transition_id, horizon_minutes) where transition_id is not null;
