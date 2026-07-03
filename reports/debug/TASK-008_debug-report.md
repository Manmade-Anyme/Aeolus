# Debug Report — TASK-008

**Date:** 2026-07-03
**Verdict:** ✅ CLEAN

## What was run

- `python -m pytest tests/ -q` — full suite (171 passed, was 146 before this task), including new live integration tests against real Supabase (`test_state_integration.py`, `test_engine_integration.py`) that actually insert/read/delete rows
- `python -m ruff check src/ tests/ config/`
- `python -m mypy src/aeolus config/`

## Observed behavior

**First module in the project doing real I/O.** `Engine` owns a `supabase-py` client directly (same access pattern as every prior storage/ingestion module — anon key, RLS disabled) and is the sole reader/writer of `signal_snapshots`/`state_transitions`, per the ADR's human-confirmed state-ownership decision.

**Config pattern changed mid-ADR, human-directed:** initial draft proposed YAML files; human asked for an exact match to ARES's `pydantic-settings` + hardcoded dual-instance pattern instead. Implemented as `config/tuning.py` (`EngineConfig`/`CategoryWeights`/`StateThresholds`, all `pydantic_settings.BaseSettings`) + `config/profiles.py` (`EXPIRY_CONFIG`/`NON_EXPIRY_CONFIG`, two complete instances). Fail-fast is structural — `test_config.py` confirms a deliberately malformed config raises at construction, not at some separate load step, because there is no load step.

**Real gap found and resolved before wiring the volatility category:** `expected_move_consumed_ratio` (spec's stated highest-value volatility signal) needs `straddle_implied_expected_move`, which requires option premium (`call_ltp`/`put_ltp`) that doesn't exist anywhere in `OptionStrike`. Flagged to the human; resolved as a VIX-based approximation (`spot_ltp * (india_vix/100) * sqrt(1/252)`), deliberately a constant one-trading-day figure rather than decayed by elapsed session time, to avoid `engine.py` ever needing to read a clock (constraint #2). See ADR Decision §7a.

**Real conflict found and resolved on the end-of-session cleanup ask:** the human's original request ("remove data at 3:31pm that's of no use") read as if it might require pruning `signal_snapshots` rows — which would break `docs/DATA_MODEL.md`'s hard constraint (Build Prompt 1: raw per-cycle data must be retroactively recomputable forever). Flagged explicitly; resolved as in-memory-only cleanup (`Engine.end_session()` clears session-scoped `EngineState` fields, never touches Supabase). See ADR Decision §7.

**Two implementation-time gaps closed, documented as ADR amendments (not silently papered over):**
1. Cross-session trailing-history granularity (one value per prior trading day, not per intraday cycle) — extended from TASK-003's own already-established "20-60 sessions" convention for consistency.
2. `previous_snapshot` always starts `None` after a mid-session restart — `option_chain` is never persisted to `signal_snapshots` by design, so it can't be reconstructed; costs exactly one cycle of TASK-005's four functions taking their existing insufficient-data path, not a new failure mode.

## Issues

| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| — | — | None found | — | — |

## Constraint audit

- [x] No per-signal veto — the weighted sum (`scorer.composite_score`) is the only path to `market_state`; a whole degraded category still contributes at its configured weight, never excluded (`test_partial_composite_missing_category_contributes_as_neutral`)
- [x] No clock-time branching — `engine.py` never calls `datetime.now()`/reads wall-clock time to *interpret* anything; `dte()` is reused verbatim from TASK-007 (consumes a caller-supplied date, doesn't compute one); `end_session()`'s market-close trigger is an external call from the (not-yet-built) scheduler, not a clock read inside this module
- [x] Deterministic reason strings — reused `template_reason` unchanged; the one new mechanism (`_extract_context`) only *reads back* its already-deterministic output, never generates new text
- [x] Polarity — inherited verbatim from TASK-003..007, no new polarity calls introduced by aggregation/hysteresis
- [x] Hysteresis/debounce — N-cycle confirmation, verified to provably prevent flapping at an oscillating threshold (`test_hysteresis_oscillating_at_threshold_never_flips`, 20 alternating cycles, zero flips) and to confirm correctly after N agreeing cycles (both unit-tested and exercised live end-to-end in `test_hysteresis_flip_writes_state_transition`)
- [x] `system_status` never mapped into `market_state` — passed through verbatim in every written row, never read by `state_for_score`/`apply_hysteresis`
