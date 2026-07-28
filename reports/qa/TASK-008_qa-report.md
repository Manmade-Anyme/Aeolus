# QA Report — TASK-008

**Date:** 2026-07-03
**Verdict:** ✅ PASS

## Test summary

| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/engine/test_scorer.py` | 12 | 12 | 0 | `category_score`, `composite_score`, `safe_call`, `state_for_score`, `apply_hysteresis` — pure, no I/O |
| `tests/engine/test_config.py` | 6 | 6 | 0 | `EngineConfig`/`CategoryWeights`/`StateThresholds` construction-time validation, both real profiles |
| `tests/engine/test_state_integration.py` | 3 | 3 | 0 | `EngineState.load()` — live Supabase, cross-session seeding + same-day restart reconstruction |
| `tests/engine/test_engine_integration.py` | 4 | 4 | 0 | `Engine.start/run_cycle/end_session` — live Supabase, end-to-end |
| Full repo suite (`pytest tests/`) | 171 | 171 | 0 | includes TASK-001..007 regression |

## Scenarios covered

Integration-style — every test calls a public function/method directly with realistic inputs; the four live tests hit the real Supabase REST API with no internal mocking, using far-off session_dates (1970/1999) so they never collide with real trading data, and clean up every row they create.

- **Aggregation:** equal-weighted category average; weighted composite from 5 category scores; a category with every sub-signal degraded to `0.5` still contributes at its configured weight (not excluded, not renormalized) — directly tests the partial-composite policy
- **`safe_call`:** success path passes through unchanged; an exception-raising sub-signal degrades to `(None, band, 0.5, "name: error (ExceptionType)")` and never propagates
- **`state_for_score`:** boundary values at both thresholds
- **Hysteresis:** N consecutive agreeing cycles confirms and flips exactly once; an oscillating proposal (alternating every cycle, 20 cycles) never accumulates enough consecutive agreement and never flips (directive's named edge case, provably not flapping); a partial streak one cycle short of confirmation does not flip; `signal_snapshots` always reflects `confirmed_state`, never the flickering `pending_state`
- **Config:** both real profiles (`EXPIRY_CONFIG`/`NON_EXPIRY_CONFIG`) construct successfully and are meaningfully distinct (different weights, expiry's GO bar is higher); a missing `reference_bands` entry, weights not summing to 1.0, out-of-order thresholds, and a missing required field all raise `ValidationError` at construction — there is no separate "load and validate" step to bypass
- **`EngineState.load()` (live):** seeds every cross-session trailing-history list and prior-day context field from a constructed prior-day row; a query against a session_date with zero prior rows (simulating first-ever go-live) seeds everything empty/`None` without raising; a same-day restart correctly rebuilds `cvd_delta_history`/`price_history`/`basis_history`/`established_range`/`cycle_price_volume_history`/`session_open`/`session_reference_price`/`confirmed_state` from that day's own rows
- **`Engine` (live):** a single `run_cycle` writes a real `signal_snapshots` row with the correct `session_date`/`config_type`/`dte`/`market_state`/`system_status`, all 16 sub-signal reasons present, all 5 category sub_scores present; a second cycle correctly threads `previous_snapshot` through to TASK-005's cycle-relative functions (`pcr_level_and_roc`'s `raw_value` is no longer `None`); forcing a pre-existing `confirmed_state="GO"` and running `confirmation_cycles` realistic cycles produces a genuine flip away from it plus exactly one `state_transitions` row; `end_session()` clears session-scoped state (`price_history`, `previous_snapshot`, `established_range`) while leaving cross-session state (`trailing_iv_history`) untouched

## Edge cases exercised

From the directive's Edge Cases section:

- **Category module returns error/missing** — `safe_call`'s exception-degradation test covers the "returns error" half explicitly; "missing" was already covered by every TASK-003..007 function's own existing tests, re-exercised here through the full `Engine.run_cycle` path in the live tests (first-cycle-of-a-session inputs are mostly insufficient-data by construction, and the pipeline still writes a valid row)
- **Score oscillating exactly at a threshold** — `test_hysteresis_oscillating_at_threshold_never_flips`, unit-level, deterministic
- **Config file invalid at startup** — structurally impossible to reach a "loaded but invalid" state; `test_config.py`'s four validation tests confirm construction itself is the gate

## Gaps / follow-ups

- `trigger_categories` on a `state_transitions` row uses a placeholder heuristic (`abs(category_score - 0.5) >= 0.1`) not specified anywhere in the spec — flagged in the ADR's Implementation Amendment as a first cut for TASK-011's eventual Discord phrasing, not a calibrated rule.
- Cross-session trailing-history granularity (one point per prior trading day) is an implementation-time decision, not something the original ADR draft nailed down — documented as an amendment, extending TASK-003's own already-established convention rather than inventing a new one.
- No test exercises `EngineState.load()` against more than 2 prior days of history (a real 60-session trailing window) — the reconstruction logic is straightforward list-building so this is a low-risk gap, but a longer-history live test would be worth adding once real trading data starts accumulating in the shared Supabase project.

---

## Post-merge fix QA — 2026-07-27: trailing-history seeding & percentile saturation

**Scope.** Verification of the fix for the live-session defect where
`gex_regime`, `cvd_direction_and_divergence` and `delta_imbalance_and_absorption`
locked at sub-score `1.00`. Full analysis in the matching debug report.

**Result.** 293 passed, 43 skipped. The 4 failures in
`tests/ingestion/test_instruments_integration.py` are pre-existing and
environmental — the sandbox proxy returns 403 for `images.dhan.co`, so the live
scrip-master fetch cannot run. Confirmed identical on the unmodified branch.
`ruff` finding count unchanged from baseline on the touched files; `mypy` reports
one pre-existing error in `ingestion/redis_client.py`, untouched here.

## Tests added

- **`test_percentile_rank_default_guard_blocks_degenerate_samples`** — the
  regression this fix exists for. A 1-element history returns neutral `0.5` from
  *both* directions (a value above and a value below the single sample), so
  neither GO nor NO-GO can be fabricated from a sample too small to support
  either. Also asserts the guard releases exactly at `MIN_PERCENTILE_HISTORY`
  and not before, so the boundary is pinned rather than incidental.
- **`test_percentile_rank_min_history_zero_does_not_divide_by_zero`** — the
  explicit `not history` check is load-bearing for `min_history=0`; without it
  that call reaches a `ZeroDivisionError`.
- **`test_gex_regime_single_session_history_does_not_saturate`** — asserts the
  reported symptom at signal level: a 1-element trailing history yields `0.75`
  (`0.5 + 0.5 * neutral`), not `1.00`. The paired assertion confirms the signal
  is still *free* to reach `1.00` on a real window — the guard suppresses
  degenerate confidence, it does not cap the signal's range.

## Fixtures corrected

Seven existing tests were asserting real semantics (magnitude scaling, polarity,
direction-agnosticism) against 1–5 element histories — the exact regime where
the degenerate behaviour hides. They were passing *because* of the bug. Widened
to windows that clear `MIN_PERCENTILE_HISTORY`, preserving each test's original
intent and expected values:

- `test_gex_regime_magnitude_scales_sub_score`
- `test_pcr_roc_realistic_computation`, `test_pcr_direction_agnostic_rising_and_falling_score_equally`
- `test_wall_proximity_polarity_far_and_decaying_scores_higher`
- `test_max_pain_drift_realistic_computation`
- `test_absorption_mid_range_weaker_baseline_lean`
- `test_vix_level_and_roc_polarity_elevated_rising_scores_higher` — needed 12
  levels rather than 5, because its `roc_history` is derived as N−1 diffs and
  must clear the guard independently of the level history.

Fixtures reference the `MIN_PERCENTILE_HISTORY` constant rather than a literal,
so they track the guard if it is ever retuned.

## Edge Cases Exercised (Post-Merge Fix)

- **Degenerate sample from both sides** — value above and below a 1-element
  history; both neutral.
- **Guard boundary** — `MIN_PERCENTILE_HISTORY - 1` neutral, `MIN_PERCENTILE_HISTORY` live.
- **`min_history=0`** — no `ZeroDivisionError` on empty history.
- **Derived-series lengths** — `vix_level_and_roc`'s N−1 `roc_history` and
  `iv_rv_spread`'s split rising/falling histories were the constraint that set
  the guard at 10 rather than 20; the VIX polarity fixture exercises the
  tightest of these.

## Gaps / Follow-ups (Post-Merge Fix)

- **The `DISTINCT ON` view itself is not covered by an automated test.** It is
  DDL executed directly against Supabase, and the existing live-DB tests are
  skipped without credentials. The Python side is verified (both call sites read
  `SignalSnapshot.EOD_VIEW`, and the fallback is exercised by inspection, not
  execution). **Verification that the view returns one row per `session_date`
  must be done manually against the real database after applying migration 0013.**
- The prior QA gap — *"No test exercises `EngineState.load()` against more than
  2 prior days of history (a real 60-session trailing window)"* — is the gap that
  let this defect through, and it is **still open**. The new tests pin
  `_percentile_rank`'s behaviour on short histories, which removes the
  *consequence*, but nothing yet asserts that `load()` actually returns 60
  distinct dates. That test needs a seeded multi-session database.
- Migration 0013 is **not** auto-applied. It must be run (or re-run — it is
  idempotent, and now also creates the index and sets `security_invoker`) in the
  Supabase SQL Editor before the fix takes effect in production.
