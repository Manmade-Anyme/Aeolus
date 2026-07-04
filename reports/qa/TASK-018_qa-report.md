# QA Report — TASK-018

**Date:** 2026-07-04
**Verdict:** ✅ PASS — migrations applied, all live tests green

## Migration-apply update (2026-07-04, same day)

Migrations `0009-0011` were hand-applied to the live Supabase project. Re-running the full live suite surfaced two real bugs, both fixed and re-verified — see the debug report's "Live verification" section for detail:

1. **Test-cleanup masking bug** (`tests/ml/test_store_integration.py`, `tests/ml/test_trainer_integration.py`, `tests/ml/test_scorer_integration.py`): a `finally`/fixture-teardown block ran two sequential deletes; when the first table didn't exist yet (pre-migration), its delete raised and silently skipped the second delete, leaking rows across many earlier blocked test runs. Confirmed via direct query: `ml_feature_store`/`ml_model_registry`/`ml_anomaly_scores` were clean (0 rows — those tables never existed to leak into), but `signal_snapshots` had 10 orphaned synthetic rows (`session_date=2030-03-01`) from the store test. Fixed by making every multi-step cleanup independently guarded (`_safe()` helper / nested `try/finally`) so one failure can never mask another. The 10 orphaned rows were deleted (user-authorized, exact tracked IDs).
2. **Test-fixture ordering bug** (`tests/ml/test_scorer_integration.py`): the `normal → spike → hover → clear` sequence test reused the same `calm`/`spike` snapshot objects for two different cycles each, giving them identical `ts` values — Postgres doesn't guarantee stable ordering for ties, so the verification query's `.order("ts")` occasionally returned cycles out of sequence. Fixed by giving each of the 5 cycles its own snapshot instance with a distinct, monotonically increasing `ts`.

Both bugs were in test code, not production code (`scorer.py`/`store.py`/`trainer.py` untouched by this update).

## Test summary
| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/ml/test_scorer.py` (no DB) | 11 | 11 | 0 | `AnomalyState` debounce/hysteresis/min-dwell/boundary behavior, `_top_features` ranking |
| `tests/ml/test_scorer_integration.py` (live) | 6 | 6 | 0 | normal→spike→hover→clear sequence (exact `flagged` order + `top_features` placement verified), STALE gating, missing-feature gating, no-registry no-op, sklearn-version mismatch, feature_set_version mismatch |
| Full `tests/ml/` + `tests/jobs/test_retention_integration.py` | 48 | 48 | 0 | All of TASK-014/016/017/018's live suites, now unblocked |
| Full repo suite | 281 | 280 | 1 (pre-existing, unrelated) | No regressions from this task |

## Scenarios covered
- **Normal stream → zero events:** a calm (low-score) cycle as the very first call produces no event.
- **Spike → single debounced ANOMALY_ENTER:** confirmed both via pure unit test (contrived scores) and live against a real fitted `IsolationForest` — a second identical-score spike cycle produces no re-fire.
- **Hover, no flapping:** a score strictly between `clear_threshold` and `flag_threshold` while already flagged holds state, no event.
- **Drop below clear → single ANOMALY_CLEAR:** symmetric to entry, debounced the same way; the live test's full `ml_anomaly_scores` row sequence now verifies exactly `[False, True, True, True, False]` with `top_features` populated only on the two transition rows.
- **Boundary behavior:** `score == flag_threshold` enters (`>=`); `score == clear_threshold` does NOT exit (`<` is strict).
- **min_dwell:** off by default (immediate flip); when set to N, blocks any flip until the current state has held N cycles, tested for both entry and clear directions.
- **STALE/missing-feature/no-registry:** each produces no row and no event.
- **Version mismatch (sklearn / feature_set_version):** treated as no-model, no row, loud warning log (asserted via `caplog`).

## Gaps / follow-ups
- **`top_features` population is ADR-scoped to "on transitions only"** — `models.py`'s own docstring says "None when not flagged" (i.e., every cycle while anomalous), a narrower reading than TASK-018's ADR ("populated on transitions only"). Followed the task-specific ADR as authoritative per this repo's convention (task ADRs supersede the broader model-level comment where they conflict) — flagging here in case TASK-019/020 need the wider behavior later.
- `train_all`-style per-config independence isn't a concept here (scorer only ever handles one `config_type` per call, driven by the snapshot itself) — no analogous test needed.
