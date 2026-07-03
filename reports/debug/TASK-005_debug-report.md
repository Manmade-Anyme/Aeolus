# Debug Report — TASK-005

**Date:** 2026-07-03
**Verdict:** ✅ CLEAN

## What was run

- `python -m pytest tests/ -q` — full suite (96 passed, was 73 before this task)
- `python -m ruff check src/aeolus/signals tests/signals`
- `python -m mypy src/aeolus/signals`

## Observed behavior

`src/aeolus/signals/oi_structure.py` added on top of the merged TASK-003/004 modules, reusing
`SignalResult`/`_percentile_rank` from `signals/contract.py`. No ingestion changes needed —
`futures_ltp` (used as the buildup-classification price-direction signal, per the human's
resolution of the ADR's blocking dependency) has been in `IngestionSnapshot` since TASK-002.

**Blocking-dependency resolution before any code was written:** the ADR originally asked
whether to add `call_ltp`/`put_ltp` per strike to upgrade buildup classification from a proxy
to true per-option premium+OI. Human declined the ingestion amendment and directed that
`oi_buildup_classification` use `futures_ltp` direction (not `spot_ltp`, as the ADR's original
Decision section had proposed) — this is the standard NSE F&O "buildup" convention, not a
weaker fallback. ADR amended accordingly (see "Blocking Dependencies — RESOLVED" section)
before `oi_structure.py` was written.

## Issues

| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| — | — | None found | — | — |

## Constraint audit

- [x] No per-signal veto present — sub-scores only, no state decision here
- [x] No clock-time branching in signal logic — verified programmatically (`test_no_function_takes_a_clock_or_datetime_argument`); "previous" is the caller-supplied prior cycle's snapshot, never a fixed wall-clock duration computed inside this module; `session_open_max_pain` is a passed-in value, same discipline as TASK-003's `session_reference_price`
- [x] Reason strings deterministic — reused `template_reason` stub, same determinism guarantee, `context` param exercised for PCR level and wall strength trend
- [x] Polarity check: GO favors option buying — direction-agnostic-movement tests for PCR ROC and max-pain drift (either direction scores equally), buildup-vs-unwind bucket test, wall proximity/strength magnitude-only test (mirrors TASK-004's flip-distance design)
- [x] `system_status` never feeds `market_state` — n/a, no concept of either here
