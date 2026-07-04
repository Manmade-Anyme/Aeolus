# Debug Report — TASK-014

**Date:** 2026-07-04
**Verdict:** ✅ CLEAN (blocked only on a human manual-DDL step, not a code issue)

## What was run
- `pytest tests/ml/ -q` — 7 pydantic contract tests, no DB.
- `pytest tests/jobs/test_retention_integration.py -q` — 3 live-Supabase tests; **errored** with `PGRST205 Could not find the table 'public.ml_model_registry'` because migrations `0009..0011` have not yet been hand-applied via the Supabase SQL Editor (anon key cannot run DDL, per TASK-001 convention — same gate every prior migration went through).
- `ruff check` + `mypy` on `src/aeolus/ml/`, `src/aeolus/jobs/retention.py`, `tests/ml/`, `tests/jobs/test_retention_integration.py` — clean.

## Observed behavior
Contract tests: `7 passed`. Integration tests: 3 errors, all the same root cause (missing tables), not a logic fault — confirmed by inspecting the Supabase error payload (`PGRST205`, schema-cache miss).

## Issues
| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| 1 | Blocker (human action, not code) | Migrations 0009–0011 not yet applied to the live Supabase project | `supabase/migrations/0009_ml_feature_store.sql` etc. | Open — needs human to run via Dashboard SQL Editor |

## Constraint audit
- [x] No per-signal veto present — n/a, this module is schema + a count/age-based cleanup job, no scoring
- [x] No clock-time branching in signal logic — n/a; `RetentionJob` reads `session_date` as a plain cutoff arithmetic input, never branches on wall-clock "what time is it"
- [x] Reason strings deterministic — n/a, no reason strings in this task
- [x] Polarity check: GO favors option buying — n/a
- [x] `system_status` never feeds `market_state` — n/a; confirmed `RetentionJob` never touches `signal_snapshots.market_state`/`system_status` columns, only `ts` for age filtering
