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
