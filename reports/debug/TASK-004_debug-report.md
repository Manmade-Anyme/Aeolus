# Debug Report — TASK-004

**Date:** 2026-07-03
**Verdict:** ✅ CLEAN

## What was run

- `python -m pytest tests/ -q` — full suite (73 passed, was 61 before this task)
- `python -m ruff check src/aeolus/signals tests/signals`
- `python -m mypy src/aeolus/signals`

## Observed behavior

`src/aeolus/signals/gamma.py` added cleanly on top of TASK-003's merged `signals/contract.py`.
`_clamp01` promoted from `volatility.py` into `contract.py` (was duplicated logic between
`iv_rv_spread`'s expected-move scaling and gamma's distance scaling) — `volatility.py` now
imports it rather than defining its own copy; no behavior change, verified by the unchanged
volatility test results.

Both functions consume only `option_chain` + `spot_ltp`, already present in `IngestionSnapshot`
since TASK-002 — no ingestion gap this time (unlike TASK-003's VIX gap).

## Issues

| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| — | — | None found | — | — |

## Constraint audit

- [x] No per-signal veto present — sub-scores only, no state decision here
- [x] No clock-time branching in signal logic — verified programmatically (`test_no_function_takes_a_clock_or_datetime_argument`); early-session instability handling explicitly deferred to TASK-008/TASK-013 config, not implemented in `gamma.py` per the ADR
- [x] Reason strings deterministic — reused `template_reason` stub from TASK-003, same determinism guarantee
- [x] Polarity check: GO favors option buying — sign convention test (`test_gex_regime_sign_convention_call_dominant_vs_put_dominant`) and magnitude-only distance test (`test_spot_distance_from_flip_polarity_further_from_flip_scores_higher`) both assert the ADR's documented direction
- [x] `system_status` never feeds `market_state` — n/a, no concept of either here
