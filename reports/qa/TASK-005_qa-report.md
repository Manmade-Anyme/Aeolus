# QA Report — TASK-005

**Date:** 2026-07-03
**Verdict:** ✅ PASS

## Test summary

| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/signals/test_oi_structure_integration.py` | 23 | 23 | 0 | all 4 public functions + `_pcr`/`_classify_buildup`/`_walls`/`_max_pain` helpers |
| Full repo suite (`pytest tests/`) | 96 | 96 | 0 | includes TASK-001/002/003/004 regression |

## Scenarios covered

Integration-style only — every test calls a public function (or the four documented private
helpers) directly with realistic `IngestionSnapshot`/`OptionStrike` inputs; no mocking of
internals.

- `pcr_level_and_roc`: realistic ROC computation, zero-call-OI degrade, first-cycle fallback, direction-agnostic scoring (rising and falling PCR of equal magnitude score identically)
- `oi_buildup_classification`: all four classification cells verified via `_classify_buildup` directly (parametrized), full-buildup scenario through the public function, missing-`futures_ltp` fallback, first-cycle fallback
- `oi_wall_proximity_and_strength`: realistic wall selection + proximity distance, strength-trend context surfaced when a `previous` snapshot has the same wall strike, magnitude-only polarity (further from wall scores higher regardless of side), missing-spot fallback
- `max_pain_drift`: hand-computed 3-strike symmetric chain (payout table verified by hand), realistic drift computation, missing-`session_open_max_pain` fallback
- `_pcr`, `_walls`: degrade/selection correctness in isolation

## Edge cases exercised

From the directive's Edge Cases section:

- **First snapshot of the day (no previous state)** — `test_pcr_first_cycle_no_previous_falls_back`, `test_buildup_first_cycle_no_previous_falls_back`, `test_max_pain_drift_no_session_reference_falls_back`
- **Strikes entering/leaving the tracked window** — `test_buildup_strike_set_mismatch_excludes_uncommon_strikes` (a strike only in `current` and one only in `previous` are both excluded, not fabricated)
- **OI update lag from exchange** — not detectable at this layer per the ADR (no per-strike freshness timestamp exists); noted as an inherited, accepted ingestion-contract limitation, not something this module can special-case

## Gaps / follow-ups

- `oi_buildup_classification` ships on the futures-price-direction convention permanently, per explicit human decision (2026-07-03) resolving the ADR's blocking dependency — not a placeholder pending a future ingestion amendment.
- Max-pain computation is O(strikes²); fine at typical NIFTY chain sizes (~40-80 strikes), flagged in the ADR as worth revisiting if reused against a much wider multi-expiry chain later.
