# QA Report — TASK-018

**Date:** 2026-07-04
**Verdict:** ⚠️ CONDITIONAL PASS — state machine fully verified (unit + live-scoring sanity run); live-Supabase suite pending human DDL apply (same gate as TASK-014/016/017)

## Test summary
| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/ml/test_scorer.py` (no DB) | 11 | 11 | 0 | `AnomalyState` debounce/hysteresis/min-dwell/boundary behavior, `_top_features` ranking |
| `tests/ml/test_scorer_integration.py` (live) | 6 | 1 | 5 (blocked) | normal→spike→hover→clear sequence, STALE gating, missing-feature gating, no-registry no-op (**passes now**), sklearn-version mismatch, feature_set_version mismatch |
| Fake-client sanity run (not committed) | — | — | — | Confirmed exact score round-trip against a real fitted `IsolationForest` and caught a synthetic-fixture bug before it reached the committed suite |

## Scenarios covered
- **Normal stream → zero events:** a calm (low-score) cycle as the very first call produces no event.
- **Spike → single debounced ANOMALY_ENTER:** confirmed both via pure unit test (contrived scores) and the live sanity run (real fitted model, exact score round-trip) — a second identical spike cycle produces no re-fire.
- **Hover, no flapping:** a score strictly between `clear_threshold` and `flag_threshold` while already flagged holds state, no event — unit-tested directly and exercised live via the picked `hover_idx` training row.
- **Drop below clear → single ANOMALY_CLEAR:** symmetric to entry, debounced the same way.
- **Boundary behavior:** `score == flag_threshold` enters (`>=`); `score == clear_threshold` does NOT exit (`<` is strict) — both pinned by dedicated unit tests, matching the ADR's explicit boundary documentation.
- **min_dwell:** off by default (immediate flip); when set to N, blocks any flip until the current state has held N cycles, tested for both entry and clear directions.
- **STALE/missing-feature/no-registry:** each produces no row and no event; the no-registry case additionally proves the "registry unreachable degrades to no-op" containment live, since the table doesn't exist yet.
- **Version mismatch (sklearn / feature_set_version):** treated as no-model, no row, loud warning log (sklearn-mismatch case additionally asserts on `caplog` that the log message fired).

## Edge cases exercised
- The synthetic-fixture `gex_magnitude` sign bug (see debug report) — worth flagging precisely because it's the kind of subtle mismatch between "what a test generates" and "what production code actually computes" (`extract_features`'s `abs()`) that would have produced flaky/wrong live-test assertions once migrations land, undetected until then. Caught early via the sanity run specifically because that run doesn't depend on the pending migrations.
- Exact score fidelity (not just "close enough"): `score_cycle`'s per-row score matched the pre-computed batch score to `< 1e-9`, confirming `standardize()` + single-row `score_samples()` reproduce the training-time computation bit-for-bit.

## Gaps / follow-ups
- **Same migration-apply blocker as TASK-014/016/017**: 5 of this task's 6 live tests are blocked; recommend applying all pending migrations in one sitting and re-running `pytest tests/ml/ tests/jobs/test_retention_integration.py -q` (now 17 previously-blocked live tests across four tasks).
- **`top_features` population is ADR-scoped to "on transitions only"** — `models.py`'s own docstring says "None when not flagged" (i.e., every cycle while anomalous), a narrower reading than TASK-018's ADR ("populated on transitions only"). Followed the task-specific ADR as authoritative per this repo's convention (task ADRs supersede the broader model-level comment where they conflict, same precedent as TASK-014 superseding the ML spec's blanket retention language) — flagging here in case TASK-019/020 need the wider behavior later.
- `train_all`-style per-config independence isn't a concept here (scorer only ever handles one `config_type` per call, driven by the snapshot itself) — no analogous test needed.
