# Debug Report — TASK-018

**Date:** 2026-07-04
**Verdict:** ✅ CLEAN (blocked only on the carried-over migration-apply step)

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
| 2 | Blocker (human action, carried over) | Migrations 0009–0011 still not applied to the live Supabase project | `supabase/migrations/0009_ml_feature_store.sql` etc. | Open |

## Constraint audit
- [x] No per-signal veto — n/a; the anomaly flag is a single IF-score-vs-threshold decision, not a per-feature veto (z-scores only populate `top_features` for explanation, never participate in the enter/clear decision — verified by code inspection: `AnomalyState.step` takes only `score`/`flag_threshold`/`clear_threshold`, no per-feature args)
- [x] No clock-time branching — the only wall-clock read in `scorer.py` is `time.monotonic()` for the model-cache TTL, an elapsed-duration mechanism (same category as TASK-002's ingestion staleness heartbeat, TASK-013 precedent), never a "what time of day is it" interpretation
- [x] Reason strings deterministic — n/a, `top_features` here is data (`{name, z}` pairs); the deterministic reason *string* templating is TASK-019's scope
- [x] Polarity — n/a
- [x] `system_status` never feeds `market_state` — n/a; `system_status` gates extraction (via TASK-015) same as always, never touches `market_state`
- [x] Never writes engine tables — grep confirms `scorer.py` only references `MLModelVersion.TABLE` (read) and `MLAnomalyScore.TABLE` (write), no `signal_snapshots`/`state_transitions`/etc.
- [x] Flag decided by IF score vs threshold only — `AnomalyState.step`'s signature takes a single scalar `score`, never a feature-level input
