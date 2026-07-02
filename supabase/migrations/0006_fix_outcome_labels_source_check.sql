-- TASK-001 fix: outcome_labels_has_source (0005) conflicts with the FK's
-- ON DELETE SET NULL. If a label has only snapshot_id set (no transition_id),
-- deleting that snapshot triggers SET NULL on snapshot_id — which then leaves
-- both source columns null and trips the check constraint, so Postgres rejects
-- the delete outright. Found via tests/storage/test_supabase_integration.py
-- (test_outcome_label_snapshot_delete_sets_null_not_cascade) against the live
-- Supabase project, 2026-07-03.
--
-- The "at least one source" invariant is still enforced at write-time by the
-- OutcomeLabel pydantic model (src/aeolus/storage/models.py, _has_source
-- validator) — TASK-012 is the only writer of this table and always sets a
-- source on insert. Dropping the DB-level check lets ON DELETE SET NULL work
-- as originally intended: an accidental upstream delete degrades a label to
-- an orphaned-but-preserved row instead of blocking the delete.

alter table outcome_labels drop constraint if exists outcome_labels_has_source;
