# QA Report — TASK-007

**Date:** 2026-07-03
**Verdict:** ✅ PASS

## Test summary

| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/signals/test_context_integration.py` | 22 | 22 | 0 | all 5 public functions (`dte`, `build_volume_price_histogram`, `value_area`, `prior_day_profile_shape`, `gap_classification`, `futures_basis_drift`) |
| `tests/ingestion/test_models.py`, `test_oi_structure_integration.py`, `test_order_flow_integration.py` | updated | — | — | new `expiry_date` field threaded through existing `IngestionSnapshot` constructions |
| `tests/ingestion/test_service_integration.py` | updated (live) | — | — | asserts `snapshot.expiry_date is not None` against the real Dhan API this session |
| Full repo suite (`pytest tests/`) | 146 | 146 | 0 | includes TASK-001..006 regression |

## Scenarios covered

Integration-style only — every test calls a public function directly with realistic inputs; no mocking of internals.

- `dte`: correct subtraction, `None` expiry → `None`, zero DTE on expiry day itself, negative DTE for a stale past expiry
- `build_volume_price_histogram`: multi-price cycle history buckets and accumulates correctly at a given `bucket_size`
- `value_area`: empty histogram → `None`; POC correctness against a synthetic 5-bucket distribution; the returned band actually holds ≥ `area_pct` of total volume (computed, not hand-verified against one fixed expectation); single-bucket degenerate case
- `prior_day_profile_shape`: missing-input and zero-width-range fallbacks; constructed trend-day case (range expansion + close near the extreme) scores GO-favorable and returns `DayProfileShape.TREND`; constructed balanced-day case (normal expansion, mid-range close) scores NO-GO-favorable and returns `DayProfileShape.BALANCED`
- `gap_classification`: missing-input fallback; `no_gap` (open inside yesterday's value area); `gap_and_go` (open beyond the VA, continuing away); `gap_and_fill` (open beyond the VA, reverting back toward it) — all three polarities verified
- `futures_basis_drift`: insufficient-history fallback; flat price_trend scores neutral; confirming vs diverging polarity, mirroring the already-tested `cvd_direction_and_divergence` pattern

## Edge cases exercised

From the directive's Edge Cases section:

- **Expiry shifted by holiday** — not a case this module handles directly (by design, per the ADR): `dte()` consumes whatever `expiry_date` Dhan's holiday-aware `expiry_list` resolution already produced, so a holiday-shifted expiry is transparent to this function. Verified the sourcing chain (`feed_rest.py` → `service.py` → `IngestionSnapshot.expiry_date`) rather than re-testing NSE holiday logic that lives entirely outside this repo.
- **First session after go-live (no stored prior day)** — `test_profile_shape_missing_input_falls_back` and `test_gap_classification_missing_input_falls_back` cover the `prior_value_area is None` / all-`prior_*`-`None` path for both functions that depend on prior-day state.
- **Gap classification before value area is computable** — covered by the same missing-input fallback path; there's no separate "still computing" state, the value area is either seeded (prior session complete) or absent (first day).

## Gaps / follow-ups

- `gap_classification`'s `no_gap` branch uses a fixed `sub_score = 0.45` (documented as an ADR Implementation Amendment) rather than a magnitude-scaled formula, since the ADR's pseudocode didn't specify one for this branch. A proximity-to-value-area-center refinement is a reasonable v2 candidate if this proves too coarse.
- Cross-session persistence (seeding `prior_day_high`/`prior_close`/`prior_value_area`/etc. from the previous session's final `signal_snapshots` row, and building `cycle_price_volume_history` cycle-by-cycle across a live session) is entirely TASK-008/013's responsibility per the ADR's Blocking Dependency #3 — not exercised here, same as TASK-006 left `cvd_delta_history` seeding to those same ADRs.
- No live-market smoke test of the histogram/value-area math itself this session (would need a live multi-cycle price/volume sequence) — reasonable for pure functions with no I/O; the one live check performed (`expiry_date` threading) targeted the actual ingestion gap this task found, not the signal math.
