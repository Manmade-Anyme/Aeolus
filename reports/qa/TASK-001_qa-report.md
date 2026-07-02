# QA Report — TASK-001

**Date:** 2026-07-03
**Verdict:** ✅ PASS on model layer. DB-level suite written but **not executed** — user explicitly chose to proceed to PR without it (no `TEST_DATABASE_URL` supplied). Migrations have never run against a real Postgres/Supabase instance. See Gaps.

## Test summary

| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/storage/test_models.py` | 16 | 16 | 0 | All 4 pydantic models, all fields, both valid/invalid paths |
| `tests/storage/test_migrations.py` | 5 | 0 (skipped) | 0 | DDL, idempotency, enum separation, FK behavior — requires `TEST_DATABASE_URL` |

## Scenarios covered

Model layer (all integration-style against the public pydantic contract, no internal mocking):
- `SignalSnapshot` round-trip via `model_dump`/`model_validate`, composite recomputable from `raw_readings` alone
- `market_state`/`system_status` settable independently (one dead-feed value doesn't imply the other)
- Invalid `market_state` values rejected (`GOOD`, lowercase, empty, `None`)
- `StateTransition` construction with trigger categories + reason
- `DailyOutlook.trend_exhaustion_flag` is a top-level field, not nested in `contributing_inputs`; `realized_archetype` defaults to `None` and accepts backfill
- `OutcomeLabel` rejects construction with neither `snapshot_id` nor `transition_id`; accepts snapshot-only source
- `OutcomeLabel.horizon_minutes` rejects non-{15,30,60} values
- `TABLE` class var on each model matches its migration filename's table name

DB layer (written, matches ADR Definition of Done, not yet run — see Gaps):
- Migrations apply cleanly to a fresh schema and are idempotent on re-run
- `market_state`/`system_status` are structurally distinct Postgres types (cross-type comparison raises `DatatypeMismatch`)
- `signal_snapshots` round-trip preserves nested `raw_readings`/`sub_scores` jsonb
- `daily_outlook.trend_exhaustion_flag` is `boolean`, its own column
- Deleting a `signal_snapshots` row sets the referencing `outcome_labels.snapshot_id` to `NULL` (not cascade-delete)

## Edge cases exercised

From TASK-001 directive's Edge Cases section:
- **Re-running migrations (idempotency)** — covered by `test_migrations_apply_cleanly_and_are_idempotent` (applies all 5 files twice, second pass must be a no-op)
- **Timezone handling (IST unambiguous storage)** — all migration timestamp columns are `timestamptz`; model layer uses `datetime` (tz-aware in tests); no naive-timestamp path exists in the schema to exercise as a failure case

## Gaps / follow-ups

- **DB-level suite not executed — deliberate, user-approved gap.** `.env` has `SUPABASE_URL`/`SUPABASE_KEY` (REST API creds) but no direct Postgres connection string, which `psycopg`-based DDL tests need. User chose to proceed to PR/merge without adding `TEST_DATABASE_URL` first. **Recommend running `pytest tests/storage/test_migrations.py -v` against the real Supabase project before or shortly after this merges to `main`**, since TASK-002+ depend on this schema actually existing and matching the SQL as written.
- Migrations have never been applied to an actual Supabase project. "Migrations apply cleanly to a fresh Supabase project" (directive acceptance criterion) is **unverified**.
- No test exercises concurrent/conflicting writes (not in directive's edge cases, not expected at this layer — append-only single-writer-per-table by design in later tasks).
