# Debug Report — TASK-018

**Date:** 2026-07-04
**Verdict:** ✅ CLEAN — migrations applied same day, all live tests re-run and green

## Live verification (2026-07-04, migrations 0009-0011 applied)

Human applied the three pending migrations via the Supabase SQL Editor. Re-running `pytest tests/ml/ tests/jobs/test_retention_integration.py -q` surfaced 2 real bugs (both in test code, not production code), fixed and re-verified:

1. **Cleanup masking bug**, found via `tests/ml/test_store_integration.py::test_sync_eod_copies_unstored_snapshots_and_is_idempotent` failing with `written == 6` instead of `1`. Root cause: that test's (and `test_trainer_integration.py`'s and `test_scorer_integration.py`'s) `finally`/teardown blocks ran two sequential deletes; across many earlier blocked runs (pre-migration), the *first* delete (against a table that didn't exist yet) raised, which — per ordinary Python `finally`-block semantics — aborted the block before the *second* delete could run, silently leaking rows every time. Confirmed by direct query: `ml_feature_store`/`ml_model_registry`/`ml_anomaly_scores` were completely empty (0 rows — nothing ever got INTO those since they didn't exist), but `signal_snapshots` (a table that existed since TASK-001) had accumulated 10 orphaned synthetic rows (`session_date=2030-03-01`) from repeated blocked runs of the store test. Fixed all three files' cleanup logic so each delete is independently guarded (nested `try/finally` or a small `_safe()` wrapper) — one table's cleanup failing can never again mask another's. The 10 orphaned rows (exact tracked IDs from the diagnostic query) were deleted with explicit user authorization.
2. **Test-fixture `ts`-tie bug**, found via `test_normal_spike_hover_clear_sequence` returning `[False, False, ..., True, True]` instead of the expected `[False, True, True, True, False]` even though row count (5) and every individual `ScoreEvent` were correct. Root cause: the test reused the same `calm`/`spike` `SignalSnapshot` objects for two different cycles each, so two pairs of rows shared identical `ts` values — Postgres doesn't guarantee stable secondary ordering for `.order("ts")` ties, so the verification query's row order was occasionally wrong even though `LiveScorer`'s actual in-memory sequencing was always correct. Fixed by giving each of the 5 cycles its own snapshot instance with a distinct, monotonically increasing `ts`.

Re-ran after both fixes: `tests/ml/` + `tests/jobs/test_retention_integration.py` → **48 passed, 0 failed**. Full repo suite → **280 passed, 1 failed** (the same pre-existing, unrelated live-Dhan-API failure in `test_ingestion_service_end_to_end`, confirmed untouched by any of this work).

## What was run
- `pytest tests/ml/test_scorer.py -q` — 11 pure unit tests of `AnomalyState`/`_top_features`, no DB, no sklearn: all pass.
- `pytest tests/ml/test_scorer_integration.py -q` — 6 live-Supabase tests; 5 errored on the same `PGRST205` cause as every TASK-014-onward live test (`ml_model_registry`/`ml_anomaly_scores` not yet in the schema cache); **1 passed regardless** (`test_no_registry_row_produces_no_row_no_event`) — this test never inserts anything and only calls `score_cycle`, so `LiveScorer`'s own "registry unreachable → degrade to no-op" containment (an `except Exception` around the registry read, falling back to the cache or `None`) handled the missing table exactly like it would handle a genuine outage, live-validating that specific ADR requirement even before the migrations land.
- Fake-Supabase-client sanity script (scratch, not committed) driving the full `calm → spike → spike(debounce) → hover → calm(clear)` sequence end-to-end with a real fitted `IsolationForest`. Caught a real bug in the *test fixture* (not production code): the synthetic training matrix used raw (possibly negative) Gaussian noise for the `gex_magnitude` column, but `extract_features` computes `gex_magnitude = abs(gex_regime.raw_value)` — so a snapshot built from a training row with a negative value there didn't round-trip back to the exact value used to fit/score the toy model. Fixed by forcing that column non-negative (`np.abs(...)`) in the synthetic matrix before fitting, in both the live test file and the sanity script. Re-verified after the fix: `score_cycle`'s score for the spike/calm rows matched the pre-computed batch score bit-for-bit (`abs(diff) < 1e-9`), and the written `ml_anomaly_scores` rows showed the exact expected `flagged` sequence `[False, True, True, True, False]` with `top_features` populated only on the two transition rows.

## Observed behavior
`15 failed [expected, migration-pending], 263 passed` full-suite; the scorer's own 11 unit tests and 1 live "no registry" test pass unconditionally. Sanity script output confirmed exact score round-trip and correct debounce/hysteresis behavior against a real (not mocked) fitted model.

## Issues
| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| 1 | Test-fixture bug (caught, fixed pre-commit) | Synthetic training matrix allowed negative `gex_magnitude` values, which don't round-trip through `extract_features`'s `abs()` | `tests/ml/test_scorer_integration.py` (toy_model fixture) | Fixed |
| 2 | Blocker (human action, carried over) | Migrations 0009–0011 not applied to the live Supabase project | `supabase/migrations/0009_ml_feature_store.sql` etc. | **Resolved** — human applied 2026-07-04 |
| 3 | Test bug (found + fixed post-migration) | Sequential-delete cleanup blocks let one table's delete failure mask another's, leaking rows across blocked test runs | `tests/ml/test_store_integration.py`, `test_trainer_integration.py`, `test_scorer_integration.py` | Fixed |
| 4 | Test bug (found + fixed post-migration) | Reused snapshot objects gave tied `ts` values, causing unstable row ordering in a verification query | `tests/ml/test_scorer_integration.py` | Fixed |

## Constraint audit
- [x] No per-signal veto — n/a; the anomaly flag is a single IF-score-vs-threshold decision, not a per-feature veto (z-scores only populate `top_features` for explanation, never participate in the enter/clear decision — verified by code inspection: `AnomalyState.step` takes only `score`/`flag_threshold`/`clear_threshold`, no per-feature args)
- [x] No clock-time branching — the only wall-clock read in `scorer.py` is `time.monotonic()` for the model-cache TTL, an elapsed-duration mechanism (same category as TASK-002's ingestion staleness heartbeat, TASK-013 precedent), never a "what time of day is it" interpretation
- [x] Reason strings deterministic — n/a, `top_features` here is data (`{name, z}` pairs); the deterministic reason *string* templating is TASK-019's scope
- [x] Polarity — n/a
- [x] `system_status` never feeds `market_state` — n/a; `system_status` gates extraction (via TASK-015) same as always, never touches `market_state`
- [x] Never writes engine tables — grep confirms `scorer.py` only references `MLModelVersion.TABLE` (read) and `MLAnomalyScore.TABLE` (write), no `signal_snapshots`/`state_transitions`/etc.
- [x] Flag decided by IF score vs threshold only — `AnomalyState.step`'s signature takes a single scalar `score`, never a feature-level input
