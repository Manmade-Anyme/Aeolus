# QA Report — TASK-019

**Date:** 2026-07-04
**Verdict:** ✅ PASS

## Test summary
| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/ml/test_explain.py` (no DB) | 8 | 8 | 0 | ranking + tie-break determinism, golden-string format (enter + clear), empty-contributors guard, Hypothesis identical-input determinism, scorer-independence proof |
| `tests/ml/` + `tests/jobs/test_retention_integration.py` regression | 56 | 56 | 0 | TASK-014..018, unaffected |

## Scenarios covered
- **Ranking by |z| descending:** a 4-feature vector with mixed sign/magnitude z-scores returns the top-k ordered by absolute value regardless of sign, preserving the true (signed) z in the output tuple.
- **Cap at k:** requesting `k=3` from a 2-feature vector returns exactly 2, no padding/fabrication.
- **Exact-tie determinism:** two features tied at `|z|=2.0` (`iv_percentile_rank`, `composite_score`) resolve deterministically via `FEATURE_ORDER` position, not dict-iteration order (asserted against the real `FEATURE_ORDER` tuple, not a hardcoded index).
- **Golden-string pinning:** `anomaly_reason` and `clear_reason` are asserted against exact expected strings (sign, `σ` precision, `score`/`threshold` 3-decimal precision, `model v{n}` suffix) — format changes will fail loudly rather than silently drifting.
- **Empty-contributors guard:** `anomaly_reason([], ...)` raises `ValueError` rather than emitting a blank or fabricated advisory.
- **Determinism property (Hypothesis):** for arbitrary z-vectors drawn from the full `FEATURE_ORDER` domain, arbitrary score/threshold/version values, calling `anomaly_reason` twice on independently-recomputed `top_contributors` output yields byte-identical strings.
- **Scorer-independence:** `AnomalyState.step` is run to completion (producing `ANOMALY_ENTER` and a fixed `state.flagged == True`) *before* `anomaly_reason` is monkeypatched to return `"GARBAGE"` — the already-decided `state.flagged` is unchanged, directly demonstrating the ADR's required separation (explanation cannot retroactively or prospectively alter the flag decision).

## Edge cases exercised
- **Ties in |z|:** covered explicitly (see above), tie-break is total-order via `FEATURE_ORDER`, so it is stable across the full 15-feature domain (not just the two features hand-picked for the test — traced by index comparison against `FEATURE_ORDER` in the assertion itself).
- **Fewer than k features with |z| above noise:** `k` capping test covers "insufficient features"; there is no "noise floor" filter in this ADR (v1 explains whatever `top_contributors` is given — TASK-018 is the caller that decides how much of `z_by_feature` to pass in), so no separate near-zero-z filtering was implemented or needed.
- **σ computed from a near-zero training-window std:** guarded upstream at fit time (`SIGMA_FLOOR` in TASK-017/015, per the directive's own note) — `explain.py` only ever sees the resulting finite z-value, so no additional guard needed here; the Hypothesis test's domain (`floats(min_value=-10, max_value=10, allow_nan=False)`) confirms no formatting failure for any finite z.

## Gaps / follow-ups
- `model_version` here is a plain `int` (per the ADR's own contract), while `ScoreEvent.model_version_id` on the caller side (TASK-018) is a `UUID` — resolving UUID -> registry `version` int is TASK-021's (orchestration hooks) job when it wires `LiveScorer` output into `explain.py`; not a gap in this task's own contract, just a note for the next integrator.
- No live/Supabase-backed test exists for this task by design — `explain.py` is pure functions with zero I/O, consistent with TASK-015's precedent (`features.py` also has no live test file).
