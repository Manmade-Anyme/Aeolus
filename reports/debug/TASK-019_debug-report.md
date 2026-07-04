# Debug Report — TASK-019

**Date:** 2026-07-04
**Verdict:** ✅ CLEAN

## What was run
- `pytest tests/ml/test_explain.py -q` — 8 tests, including a Hypothesis determinism check and a monkeypatch-based scorer-independence proof.
- `pytest tests/ml/ tests/jobs/test_retention_integration.py -q` (regression) — 56/56 (48 prior + 8 new).
- `pytest tests/ -q` (full repo) — 288 passed, 1 failed (pre-existing, unrelated live-Dhan-API failure in `test_ingestion_service_end_to_end`, untouched by this work).
- `ruff check` + `mypy` on `src/aeolus/ml/explain.py`, `tests/ml/test_explain.py` — clean.

## Observed behavior
All 8 new tests pass on first correct implementation. No production bugs surfaced this task — `explain.py` is pure functions over an already-computed z-vector, no DB, no fitting, no state.

## Issues
| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| — | — | None found | — | — |

## Constraint audit
- [x] No per-signal veto present — n/a, pure string/ranking functions, no scoring/gating; `top_contributors`/`anomaly_reason` take the flag outcome (`score`, thresholds) as plain scalar inputs, never an `AnomalyState`
- [x] No clock-time branching in signal logic — grep confirms no `datetime.now`/`date.today`/`time.monotonic` calls anywhere in `explain.py`
- [x] Reason strings deterministic — `anomaly_reason`/`clear_reason` are pure templates over `(contributors, score, threshold, model_version)`; Hypothesis property test confirms identical inputs -> identical string across the full `FEATURE_ORDER` domain
- [x] Polarity check: GO favors option buying — n/a, advisory ML overlay, no `market_state` involvement
- [x] `system_status` never feeds `market_state` — n/a, this module never reads `system_status` or writes any table
- [x] Explanation strictly downstream of the flag decision — `test_explanation_never_influences_flag_decision` computes `AnomalyState.step`'s outcome first, then monkeypatches `anomaly_reason` to return garbage and confirms the already-computed `state.flagged` is unaffected; structurally, `explain.py` has zero imports from `scorer.py` and no function here accepts an `AnomalyState`
- [x] No LLM-narrated / free-text output — every string in `explain.py` is built from an f-string template with fixed-precision numeric formatting (`{z:+.1f}`, `{score:.3f}`), no interpolated free text
