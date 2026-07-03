# Debug Report — TASK-012

**Date:** 2026-07-03
**Verdict:** ✅ CLEAN

## What was run
- `pytest tests/jobs/ -q` — 14 unit tests (`classify_realized_archetype`, `_label`/`_nearest_within`/`_direction` helpers) + 3 live Supabase integration tests.
- `pytest -q` (full suite, 221 tests) — regression check.
- `ruff check` + `mypy` on `src/aeolus/jobs/`, `tests/jobs/`.
- Two live migrations applied by hand (this project's established DDL-application convention, anon key can't run DDL): `0007_outcome_labels_idempotency.sql`, then `0008_fix_outcome_labels_idempotency_constraint.sql`.

## Issues found and fixed during implementation
| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| 1 | High | `run()` passed `current_iv` (ATM option IV) where `implied_expected_move` needs `india_vix` (VIX index level) — two different stored quantities conflated, would have silently produced a wrong `straddle_price_change` for every label. Caught by `test_label_produces_all_horizons_when_matches_exist` returning zero labels instead of the expected 3. | `src/aeolus/jobs/backfill.py` (both call sites in `run()`) | Fixed — renamed param to `t0_india_vix`, both callers now read the `india_vix` carry key |
| 2 | High | Migration `0007`'s partial unique indexes (`WHERE snapshot_id/transition_id IS NOT NULL`) don't satisfy PostgREST's `ON CONFLICT` inference — confirmed live via `42P10: no unique or exclusion constraint matching the ON CONFLICT specification`. Postgres only matches a partial index for conflict inference if the same predicate is repeated in the `ON CONFLICT` clause, which PostgREST's upsert never does. | `supabase/migrations/0007_outcome_labels_idempotency.sql` | Fixed via `0008_fix_outcome_labels_idempotency_constraint.sql` — plain (non-partial) `UNIQUE` constraints instead, which achieve the identical guarantee (`NULL <> NULL` in standard SQL uniqueness already ignores the other entity type's rows) in a form PostgREST can target |
| 3 | Medium | ADR proposed reusing `context_signals.prior_day_profile_shape` for the whole-day directional read; that function requires `trailing_average_range_history`, which is `EngineState`-internal in-memory data never persisted to `signal_snapshots` — unusable from a standalone DB-only job. | `src/aeolus/jobs/realized_archetype.py` | Corrected at design time (before code was written) — implemented a self-contained range/close-location helper instead, documented in the ADR's "Build-time corrections" section |

## Observed behavior
Full suite: `221 passed, 47 warnings` (pre-existing unrelated deprecation warnings). `ruff`/`mypy`: no issues. All 3 live integration tests pass against the real Supabase project post-migration-0008.

## Constraint audit
- [x] No per-signal veto present — n/a, this module labels/classifies after the fact, never scores or gates live decisions
- [x] No clock-time branching — session-half split for `breakout_transition` detection is by snapshot count, not wall-clock time; forward-match horizons are elapsed-time deltas from a given `t0`, never "what time is it now"
- [x] Deterministic — `classify_realized_archetype` and the labeling helpers are pure functions of stored data, no randomness, no LLM
- [x] Polarity n/a — this module doesn't score GO/NO-GO, it labels realized outcomes for later ML use
- [x] Never runs live/synchronously with the scoring loop — `OutcomeBackfillJob` has no scheduler wiring in this task (TASK-013 doesn't exist yet); invoked explicitly, reads only already-committed `signal_snapshots`/`state_transitions` rows
