# Debug Report — TASK-003

**Date:** 2026-07-03
**Verdict:** ✅ CLEAN

## What was run

- `python -m pytest tests/ -q` — full suite (61 passed, was 37 before this task)
- `python -m ruff check src/aeolus/signals src/aeolus/explain tests/signals tests/explain`
- `python -m mypy src/aeolus/signals src/aeolus/explain`

## Observed behavior

New modules (`src/aeolus/signals/contract.py`, `src/aeolus/signals/volatility.py`,
`src/aeolus/explain/reason.py`) added cleanly on top of `main` (TASK-002 already merged,
`india_vix`/`lot_size`/`futures_basis` present on `IngestionSnapshot`). No environment
recurrence of the `.pth`/`UF_HIDDEN` issue from TASK-001/002 — `pythonpath = ["src"]` in
`pyproject.toml` continues to hold.

`vix_level_and_roc` and `expected_move_consumed_ratio` are exercised only against
constructed inputs (`current_vix=None` end-to-end, since VIX ingestion just landed in
TASK-002b and no live trailing history exists yet; `expected_move_consumed_ratio` is
live-only per directive and has no historical-data source to test against pre-go-live).
Not a gap — matches the directive's own "live-only, in this module's loop" scoping.

## Issues

| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| — | — | None found | — | — |

## Constraint audit

- [x] No per-signal veto present — these are sub-score-only functions, no state/GO-NO-GO decision made here
- [x] No clock-time branching in signal logic — verified programmatically (`test_no_function_takes_a_clock_or_datetime_argument` inspects each function's signature); `session_reference_price` is caller-supplied, never computed from wall-clock time inside this module
- [x] Reason strings deterministic (same input → same string, verified) — `test_deterministic_same_inputs_byte_identical`
- [x] Polarity check: GO favors option buying — one polarity test per sub-signal (`iv_percentile_rank`, `iv_rv_spread` rising-vs-falling, `vix_level_and_roc`, `expected_move_consumed_ratio`), all assert `sub_score` moves in the ADR-documented direction; `iv_rv_spread`'s redesigned IV-trend polarity (falling IV → NO-GO regardless of absolute level) specifically checked
- [x] `system_status` never feeds `market_state` — n/a, this module has no concept of either; pure sub-signal functions only
