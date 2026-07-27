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

---

## Post-merge fix — 2026-07-27: trailing-history seeding & percentile saturation

**Symptom (live session).** `gex_regime`, `cvd_direction_and_divergence` and
`delta_imbalance_and_absorption` sat locked at sub-score `1.00`. The pre-market
outlook simultaneously reported `IV Percentile Rank: no data`,
`Prior Day Profile: no data` and `Straddle Level vs History: 1.00`.

**Root cause.** Two independent defects compounding, both in this task's code:

1. **The seeding query could only ever see one session.** `EngineState.load()`
   read `signal_snapshots` with `.order("ts", desc=True).limit(MAX_TRAILING_SESSIONS * 20)`.
   The `* 20` encodes an assumption of ~20 rows per session. The engine actually
   writes one row per cycle at `CYCLE_INTERVAL_SECONDS = 5.0` over 09:15–15:30 —
   **~4,500 rows per session**. Every row in the window therefore came from the
   single most recent prior date, and `_seed_cross_session`'s dedupe-by-date
   resolved to exactly one distinct `session_date`. Every cross-session trailing
   history was permanently one element long, regardless of how many months of
   data accumulated. `OutlookGenerator._load_trailing_histories()` had the same
   bug at `limit(400)` (~9 minutes of cycles).

2. **A 1-element percentile is degenerate, not merely noisy.**
   `_percentile_rank(value, [h])` returns `1.0` if `h <= value` else `0.0` —
   never anything between. The three affected signals all have the shape
   `sub_score = 0.5 + 0.5 * magnitude_pct`, so a saturated `magnitude_pct` maps
   straight to `1.00`. The system reported maximum confidence from the smallest
   possible sample.

Defect 1 explains why the histories were short. Defect 2 explains why that
surfaced as a confident wrong answer rather than a neutral one. Both needed
fixing: the view alone leaves the saturation trap in place for any future
seeding regression, and the guard alone leaves the engine blind to 59 of its
60 sessions.

**Why it was not caught earlier.** This report's own QA gap list flagged it:
*"No test exercises `EngineState.load()` against more than 2 prior days of
history (a real 60-session trailing window)."* Every fixture in the signal
suites used 1–5 element histories, which is precisely the regime where the
degenerate behaviour is invisible — several tests were passing *because* of it
and had to be widened to realistic windows as part of this fix.

**Fix.**

| # | Change | File |
|---|---|---|
| 1 | `daily_eod_signal_snapshots` view — `DISTINCT ON (session_date)`, `security_invoker = on`, plus a `(session_date DESC, ts DESC)` index so the `DISTINCT ON` walks an index instead of sorting the full table | `supabase/migrations/0013_*.sql`, `supabase/schema.sql` |
| 2 | Seed from `SignalSnapshot.EOD_VIEW` (new constant, documented at the model so the raw table is not reached for by mistake) | `storage/models.py`, `engine/state.py`, `outlook/generator.py` |
| 3 | `_percentile_rank` guards on `MIN_PERCENTILE_HISTORY = 10` **by default** — safe-by-default rather than opt-in, so a new call site cannot reintroduce the failure by omission | `signals/contract.py` |
| 4 | Fallback path logs at ERROR instead of silently reinstating the broken query; degraded mode is now safe because of (3) rather than by accident | `engine/state.py`, `outlook/generator.py` |
| 5 | `OutlookGenerator.MAX_TRAILING_SESSIONS` 20 → 60, matching the engine and clearing `iv_percentile_rank`'s 20-session floor with margin | `outlook/generator.py` |

**Why `MIN_PERCENTILE_HISTORY = 10` and not `MIN_LOOKBACK_SESSIONS = 20`.**
Several callers rank against *derived* series that are structurally shorter than
the session count: `vix_level_and_roc`'s `roc_history` is N−1 diffs, and
`iv_rv_spread`'s rising/falling magnitude histories split those N−1 diffs across
two lists (~N/2 each). Gating those at 20 would pin them to `0.5` permanently —
trading stuck-at-1.00 for stuck-at-0.5, which is quieter but no more informative.
10 is reachable inside the 60-session seed while still too large to saturate.

## Issues

| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| 1 | High | Trailing-history seed window covered one `session_date`, never more | `engine/state.py:94`, `outlook/generator.py:165` | Fixed |
| 2 | High | `_percentile_rank` saturated to 0.0/1.0 on degenerate samples | `signals/contract.py:21` | Fixed |
| 3 | Medium | View read failure silently fell back to the known-broken query | `engine/state.py:104`, `outlook/generator.py:176` | Fixed |
| 4 | Low | Outlook's 20-session window sat exactly on `MIN_LOOKBACK_SESSIONS` | `outlook/generator.py:25` | Fixed |
| 5 | Low | View lacked a supporting index and `security_invoker` | `supabase/migrations/0013_*.sql` | Fixed |

## Constraint audit

- [x] **No per-signal veto** — unchanged. The guard alters a sub-score's *value*, never routes around the composite. A guarded signal contributes `0.5` at its configured weight like any other neutral reading.
- [x] **No clock-time branching** — no time reads added; the seeding queries take a caller-supplied `session_date`, as before.
- [x] **Deterministic reason strings** — `template_reason` untouched; reason strings remain a pure function of `(raw_value, reference_band, sub_score)`.
- [x] **Polarity** — unchanged. The guard returns the neutral `0.5` midpoint, which is directionless by construction and cannot bias GO or NO-GO.
- [x] **`system_status` vs `market_state`** — the fallback logs and degrades signals to neutral; it never writes a `market_state`, so a storage fault still cannot masquerade as NO-GO.
