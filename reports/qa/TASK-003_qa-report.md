# QA Report — TASK-003

**Date:** 2026-07-03
**Verdict:** ✅ PASS

## Test summary

| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/signals/test_volatility_integration.py` | 20 | 20 | 0 | all 4 public functions + `_atm_strike`/`_resolve_atm_iv` helpers |
| `tests/explain/test_reason.py` | 4 | 4 | 0 | `template_reason` determinism, no-data path, context param |
| Full repo suite (`pytest tests/`) | 61 | 61 | 0 | includes TASK-001/002 regression |

## Scenarios covered

Integration-style only — every test calls a public function (or the two documented
private helpers) directly with realistic `OptionStrike`/float inputs; no mocking of
internals, no call-count assertions.

- `iv_percentile_rank`: realistic percentile computation against a 20-session history, polarity (higher value → higher score), missing-current-iv fallback
- `iv_rv_spread`: rising/falling/flat IV-trend polarity (redesigned scoring per ADR), `raw_value` confirmed as signed trend not spread level, RV-spread context present/absent depending on `trailing_spot_history` length, first-cycle (`previous_iv=None`) fallback
- `vix_level_and_roc`: `current_vix=None` end-to-end (VIX not yet wired into a live trailing history), empty-history no-crash path, elevated+rising vs quiet+falling polarity
- `expected_move_consumed_ratio`: missing-input fallback, zero-expected-move fallback, raw-value arithmetic, band-clamped polarity
- `_atm_strike` / `_resolve_atm_iv`: closest-strike selection, empty chain, bad-leg (zero/negative IV) dropped without falling back to a nearby strike
- `template_reason`: byte-identical output on repeated calls, explicit `"{name}: no data"` on `None`, context dict appended without altering the scored fields

## Edge cases exercised

From the directive's Edge Cases section:

- **Insufficient trailing history early after go-live** — `test_iv_percentile_rank_insufficient_history_falls_back` (<20 sessions → `(None, band, 0.5, reason)`, not a computed percentile)
- **IV missing for a strike** — `test_resolve_atm_iv_drops_bad_leg_no_fallback_to_nearby_strike`
- **VIX unavailable** — `test_vix_level_and_roc_missing_vix_falls_back_end_to_end`

## Gaps / follow-ups

- `vix_level_and_roc` and `expected_move_consumed_ratio` have no live-market exercise yet — VIX trailing history and live straddle pricing don't exist until the scheduler (TASK-013) is wired up and running against real sessions. Not blocking: both degrade cleanly on `None` inputs today, which is the only reachable state pre-go-live.
- Trailing-history *sourcing* (who queries `signal_snapshots.raw_readings` and shapes it into these functions' `list[float]` arguments) is explicitly out of scope for TASK-003 (ADR "Blocking Dependencies" #3) — deferred to whichever of TASK-008/TASK-013 claims it.
