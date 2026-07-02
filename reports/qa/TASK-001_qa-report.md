# QA Report — TASK-001

**Date:** 2026-07-03 (updated — live DB verification closed out)
**Verdict:** ✅ PASS. Both suites green: model layer + live Supabase integration.

## Test summary

| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/storage/test_models.py` | 16 | 16 | 0 | All 4 pydantic models, all fields, both valid/invalid paths |
| `tests/storage/test_supabase_integration.py` | 8 | 8 | 0 | Live against real Supabase project via `supabase-py` + anon key; DDL, enum enforcement, FK behavior, unique/check constraints |

`tests/storage/test_migrations.py` (psycopg/direct-Postgres approach) removed — that access method was never available in this environment (no DB password, no Supabase CLI login). Replaced by `test_supabase_integration.py`, matching the `supabase-py` + anon-key pattern already used by the Ares project sharing this Supabase instance.

## Scenarios covered

Model layer (integration-style against the public pydantic contract, no internal mocking) — unchanged from first pass, see prior revision in git history.

Live DB layer (`test_supabase_integration.py`, each test cleans up its own rows):
- `signal_snapshots` insert → select round-trip, `raw_readings`/`sub_scores` jsonb preserved exactly
- Invalid `market_state` value rejected by the live enum type (`APIError`, not just pydantic)
- `state_transitions` insert with `trigger_categories` jsonb list
- `daily_outlook.trend_exhaustion_flag` is a real top-level boolean column, not nested; `realized_archetype` null by default
- `daily_outlook.session_date` UNIQUE constraint enforced live (duplicate insert rejected)
- `outcome_labels.horizon_minutes` CHECK rejects non-`{15,30,60}` values live
- **`outcome_labels` FK `ON DELETE SET NULL` behavior** — deleting a referenced `signal_snapshots` row correctly nulls `outcome_labels.snapshot_id` instead of cascading or blocking (only true after the `0006` fix — see Gaps/Bugs below)
- Sourceless `outcome_labels` row (both `snapshot_id`/`transition_id` null) — now DB-permitted by design, still blocked by the `OutcomeLabel` pydantic validator (covered in `test_models.py`)

## Edge cases exercised

From TASK-001 directive's Edge Cases section:
- **Re-running migrations (idempotency)** — all migration files use `if not exists` / `DO $$ ... duplicate_object` guards; re-running the full SQL Editor script is safe (verified by construction, not re-executed a second time live to avoid unnecessary load on a shared project)
- **Timezone handling (IST unambiguous storage)** — all timestamp columns `timestamptz`; live inserts used `datetime.now(timezone.utc).isoformat()`, round-tripped correctly

## Bugs found and fixed during this pass

1. **`outcome_labels_has_source` CHECK vs `ON DELETE SET NULL` FK conflict** (real, DB-only — invisible to the model-only suite). See `reports/debug/TASK-001_debug-report.md` Issue #3 and ADR Amendment for full detail. Fixed via `supabase/migrations/0006_fix_outcome_labels_source_check.sql`, applied live and re-verified.

## Gaps / follow-ups

- Migrations are applied by hand via the Supabase Dashboard SQL Editor, not an automated CLI (`supabase db push`) — no Supabase CLI login or DB password available in this environment. Acceptable for now (single shared dev project, small team), but TASK-013 (orchestration) or a future ops task should reconsider this if a staging/prod split is ever introduced.
- No test exercises concurrent/conflicting writes (not in directive's edge cases, not expected at this layer).
- RLS is disabled project-wide on these 4 tables (anon key = full read/write). Matches existing convention (Ares) but is worth a security pass before this system handles anything beyond append-only signal data.
