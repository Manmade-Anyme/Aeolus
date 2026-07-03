# QA Report — TASK-004

**Date:** 2026-07-03
**Verdict:** ✅ PASS

## Test summary

| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/signals/test_gamma_integration.py` | 12 | 12 | 0 | both public functions + `_net_gamma_by_strike`/`_flip_level` helpers |
| Full repo suite (`pytest tests/`) | 73 | 73 | 0 | includes TASK-001/002/003 regression |

## Scenarios covered

Integration-style only — every test calls a public function (or the two documented private
helpers) directly with realistic `OptionStrike` inputs; no mocking of internals.

- `gex_regime`: sign convention (call-dominant → dampening/NO-GO, put-dominant → amplifying/GO), magnitude scaling at fixed sign (weak vs strong reading against different trailing-history percentile positions), missing-spot and thin-OI fallbacks
- `spot_distance_from_flip`: interpolated flip level between two bracketing strikes (hand-verified against a manually computed linear interpolation), magnitude-only polarity (further from flip → higher score, sign-independent), monotonic-chain (no crossing) → `None` not extrapolated, missing-spot and thin-OI fallbacks
- `_net_gamma_by_strike`: ascending sort order
- `_flip_level`: single-strike chain → `None` (no bracketing pair possible)

## Edge cases exercised

From the directive's Edge Cases section:

- **Flip level far outside traded strike range** — `test_spot_distance_from_flip_monotonic_chain_returns_none_not_extrapolated` (same-sign chain never crosses zero → `None`, not a guessed extrapolation); also confirms `gex_regime` is unaffected by the absent flip level, per the ADR's explicit note that the two functions are independent
- **Thin OI making GEX noisy** — `test_gex_regime_thin_oi_falls_back`, `test_spot_distance_from_flip_thin_oi_falls_back` (`min_total_oi` threshold)
- **Early-session instability of the estimate** — explicitly NOT handled in `gamma.py` per the ADR (constraint #2: no clock-time branching in signal logic); deferred to TASK-008/TASK-013 config. Confirmed by the constraint-check test that no function takes a clock/time argument.

## Gaps / follow-ups

- Both functions are only exercised against constructed chains, not a live option chain — no live-market smoke test performed this session (unlike TASK-002's live verification). Reasonable for pure functions with no I/O; would need TASK-008/013 wiring to exercise against a real chain end-to-end.
- `lot_size` correctness (a config-sourced constant, not read from `IngestionSnapshot`) is explicitly the caller's responsibility per the ADR — not validated inside `gamma.py`, flagged there as belonging to whichever module (TASK-008/013) resolves it.
