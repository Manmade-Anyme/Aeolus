# Debug Report — TASK-016

**Date:** 2026-07-04
**Verdict:** ✅ CLEAN (blocked only on the same pending migration-apply step as TASK-014)

## What was run
- `pytest tests/ml/test_store_integration.py -q` — 4 live-Supabase tests; **errored**, same root cause as TASK-014's retention tests: `PGRST205 Could not find the table 'public.ml_feature_store'` — migrations `0009..0011` still not hand-applied via the Supabase Dashboard SQL Editor.
- `pytest tests/ml/ -q` — 19 non-DB tests pass (7 models + 12 features); the 4 new store tests are the only ones needing the live schema.
- `ruff check` + `mypy` on `src/aeolus/ml/store.py`, `tests/ml/test_store_integration.py` — clean.

## Observed behavior
`19 passed, 4 errored` (same `PGRST205` schema-cache-miss pattern as TASK-014, not a logic fault). Code reviewed by hand against the ADR contract: `append` extracts via TASK-015 and refuses (returns `False`, no write attempted) when the vector is `None` or contains any `None` leaf; `sync_eod` anti-joins on `stored_snapshot_ids` before calling `append` per un-stored row, so replay is a no-op both at the upsert layer (`ignore_duplicates=True` on `source_snapshot_id`) and at the anti-join layer (belt-and-suspenders, matches the ADR's "idempotent by construction" framing); `load_window` two-query design (distinct dates first, then full rows filtered to those dates) avoids pulling the entire permanent table just to compute a window.

## Issues
| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| 1 | Blocker (human action, carried over from TASK-014) | Migrations 0009–0011 still not applied to the live Supabase project — now blocking both TASK-014's and TASK-016's live suites | `supabase/migrations/0009_ml_feature_store.sql` etc. | Open — needs human to run via Dashboard SQL Editor |

## Constraint audit
- [x] No per-signal veto — n/a
- [x] No clock-time branching — `sync_eod`/`load_window` take `session_date`/`window_days` as parameters, no internal clock read
- [x] Reason strings deterministic — n/a, no reason strings in this module
- [x] Polarity — n/a
- [x] `system_status` never feeds `market_state` — n/a; `system_status` only gates extraction (via TASK-015), never touches `market_state`
- [x] Read-only against `signal_snapshots` — `store.py` only ever `.select()`s that table, confirmed by grep (no `.insert(`/`.update(`/`.delete(` against `SignalSnapshot.TABLE` anywhere in the file)
- [x] No engine file imports `aeolus.ml` — new file only imports from `aeolus.storage.models` (existing convention) and sibling `aeolus.ml` modules
