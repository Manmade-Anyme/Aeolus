# QA Report — TASK-012

**Date:** 2026-07-03
**Verdict:** ✅ PASS

## Test summary
| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/jobs/test_realized_archetype.py` | 8 | 8 | 0 | All 4 base quadrants, both overrides, the double_distribution fallback, insufficient-data → None |
| `tests/jobs/test_backfill_helpers.py` | 6 | 6 | 0 | `_direction`, `_nearest_within` (match + no-match), `_label` (all horizons, skip-on-no-match, missing t0 data) |
| `tests/jobs/test_backfill_integration.py` (live) | 3 | 3 | 0 | Full `run()` against real Supabase — snapshot labels, transition labels, archetype backfill, idempotent re-run, missing-outlook-row non-raising |
| Full repo suite (`pytest -q`) | 221 | 221 | 0 | Regression check |

## Scenarios covered
- **Realized-archetype classifier:** all 4 base quadrants (clean_trend, grinding_trend, pinned_range, choppy_range) via the Directional×Volatility cross-table from Spec §4; `breakout_transition` override (first-half balanced, second-half trend); `event_gap` override (gap-open + mid-session IV spike-then-crush); `double_distribution` explicitly asserted to never be returned (falls through to the base quadrant, per the ADR's documented known limitation) rather than silently "passing" a test that doesn't actually check anything.
- **Outcome labeling:** forward-match within the ±5 min tolerance succeeds; a target outside tolerance is skipped (not fabricated); `realized_move`/`direction`/`straddle_price_change` computed correctly against a live 4-snapshot session with a real state transition in the middle.
- **Idempotency:** live re-run of `OutcomeBackfillJob.run()` for the same session_date produces the identical row count — verifies the corrected `0008` unique constraints actually work end-to-end through the real upsert path, not just that they exist in the schema.
- **Missing `daily_outlook` row:** `run()` completes without raising when no Outlook exists for the session_date (labels are still written; the archetype-backfill `update()` simply touches zero rows).

## Edge cases exercised
- Missing forward data for a timestamp (directive's stated edge case): covered by `test_nearest_within_tolerance_no_match` and `test_label_skips_horizon_with_no_forward_match`.
- Snapshots within 60 min of close (truncated forward window): not a special case in the code — naturally falls out of the same "no match within tolerance" path, since nothing exists past the session's last snapshot. Not separately live-tested (would need a live session artificially ending exactly N minutes before a horizon target), but the unit-level `test_label_skips_horizon_with_no_forward_match` exercises the identical code path with a single-snapshot session.
- Halted/shortened sessions: same reasoning — any gap in cadence just produces more skipped horizons via the existing tolerance check, no dedicated code path needed.

## Gaps / follow-ups
- `double_distribution` remains an accepted, documented known limitation (never detected) — flagged in the ADR as needing a persisted full-session volume-at-price histogram that doesn't exist today. Revisit if/when that data gets persisted for some other reason.
- No live test specifically forces a halted/shortened-session scenario (would need constructing an artificially sparse live session) — covered at the unit level only, per the note above.
