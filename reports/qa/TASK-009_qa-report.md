# QA Report — TASK-009

**Date:** 2026-07-03
**Verdict:** ✅ PASS

## Test summary

| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/outlook/test_archetype.py` | 10 | 10 | 0 | `score_archetypes`, `primary_and_secondary` — pure, no I/O |
| `tests/outlook/test_generator_integration.py` | 4 | 4 | 0 | `OutlookGenerator.run()` — live Supabase |
| `tests/signals/test_volatility_integration.py` (additions) | 3 | 3 | 0 | new `implied_expected_move` function |
| Full repo suite (`pytest tests/`) | 188 | 188 | 0 | includes TASK-001..008 regression |

## Scenarios covered

- **`implied_expected_move`:** correct formula against a hand-computed expectation; `None` on either missing input
- **`score_archetypes`:** all-`None` inputs produce the exact uniform `1/7` distribution (not an approximation — exact equality checked); `profile_shape="trend"` measurably shifts mass toward `grinding_trend`/`pinned_range` and away from `clean_trend` vs. the uniform baseline; `profile_shape="balanced"` shifts toward `breakout_transition`/`double_distribution`; high/low `expanding_vol_pct` favor the documented expanding/contracting-volatility archetype sets; `dte=0` boosts `pinned_range`; a GIFT gap above/below `gift_gap_threshold` does/doesn't nudge `event_gap` (the below-threshold case asserts the distribution is byte-for-byte identical to the no-gap baseline, confirming the threshold actually gates)
- **`primary_and_secondary`:** correct ranking on a constructed distribution; a fully-tied distribution breaks ties by `ARCHETYPES`' declared order, deterministically
- **`OutlookGenerator.run()` (live):** writes one `daily_outlook` row with a valid archetype/confidence/`straddle_level_vs_history`, `trend_exhaustion_flag` correctly reflecting yesterday's seeded `profile_shape="trend"`, distribution+secondary present in `contributing_inputs`, OI max-pain carryover and PCR-level carryover both populated from a constructed prior-day row; a session with zero prior rows (simulating first-ever go-live) completes without raising, `trend_exhaustion_flag=False`; running `run()` twice for the same `session_date` upserts (exactly one row, not two); a `realized_archetype` written by a simulated TASK-012 backfill survives a subsequent duplicate `run()` untouched

## Edge cases exercised

From the directive's Edge Cases section:

- **GIFT Nifty unavailable pre-open** — steady-state in v1 (`gift_nifty` structurally `None`); covered implicitly by every generator test, since none supply a non-`None` `gift_nifty`, and `score_archetypes`'s own `gift_nifty_gap=None` path is unit-tested directly
- **First session (no prior-day data)** — `test_first_session_ever_completes_without_raising`, live
- **Duplicate run same session (idempotency)** — `test_duplicate_run_upserts_not_duplicates` and `test_duplicate_run_never_clobbers_a_realized_archetype_backfill`, both live

## Gaps / follow-ups

- Every nudge factor and threshold in `score_archetypes` (0.5/1.2/1.3/1.5, the 0.7/0.3 volatility cutoffs, `gift_gap_threshold=50.0`) is an unbacktested judgment placeholder — flagged extensively in the ADR as the piece of this module most likely to need real revision once `daily_outlook.realized_archetype` (TASK-012) accumulates enough history to check against.
- No live test exercises the `gift_nifty_gap` nudge with an actual non-`None` value end-to-end through `OutlookGenerator` (only unit-tested in `archetype.py` directly) — reasonable given `gift_nifty` is structurally `None` in this codebase today; worth adding if that ever changes.
