# QA Report — TASK-006

**Date:** 2026-07-03
**Verdict:** ✅ PASS

## Test summary

| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/signals/test_order_flow_integration.py` | 25 | 25 | 0 | all 3 public functions + `_tick_classify`/`_near_extreme` helpers |
| `tests/ingestion/test_feed_ws_parsing.py` | 3 | 3 | 0 | new Full-packet fields (`volume`/`total_buy_quantity`/`total_sell_quantity`/`day_high`/`day_low`), unit-level, no live market hours needed |
| `tests/ingestion/test_models.py`, `test_service_integration.py` | updated | — | — | new `IngestionSnapshot` fields exercised in existing model/service tests |
| Full repo suite (`pytest tests/`) | 124 | 124 | 0 | includes TASK-001..005 regression |

## Scenarios covered

Integration-style only — every test calls a public function (or the two documented private
helpers) directly with realistic `IngestionSnapshot` inputs; no mocking of internals.

- `cvd_direction_and_divergence`: empty/single-point history fallback, flat price_trend (below
  the reference_band flat-threshold) scores neutral, confirming vs diverging polarity
- `_tick_classify`: price-up/price-down/price-flat sequences produce the documented
  signed/zero volume_delta attribution; missing volume/price data → `None`
- `delta_imbalance_and_absorption`: missing-input, zero-book, and zero-width-range fallbacks;
  confirms-extreme at both the low and the high; opposes-extreme (absorption) at the low;
  mid-range weaker baseline lean, formula-verified
- `volume_participation_range`: missing-input fallback; below-threshold and degenerate-range
  (too narrow) non-establishment; establishment + inside-range scoring; breakout scoring;
  `established_low`/`established_high` surfaced via `context` for caller persistence
- `_near_extreme`: zero-width range guard, both-sides detection

## Edge cases exercised

From the directive's Edge Cases section:

- **Low-volume open making the X% range degenerate** — `test_volume_range_degenerate_range_not_established` (volume threshold crossed but range narrower than `min_range_width` → stays unestablished, keeps waiting)
- **CVD reset semantics across reconnects (must not double-count)** — not a runtime test (would need a live reconnect), but resolved by construction per the ADR: `volume_delta` derives from Dhan's own exchange-side cumulative counter, verified by code inspection of `_tick_classify` and cross-checked against TASK-002's existing reconnect-handling discipline

## Gaps / follow-ups

- `volume_participation_range`'s `established_range` caller-persistence mechanism (via `context`) is a genuine gap-fill in the ADR, not something the ADR fully specified — flagged in both the ADR amendment and the debug report for TASK-008/013 to formalize when their ADRs are written.
- No live-market smoke test this session (unlike TASK-002's original live verification) — reasonable for pure functions with no I/O; the ingestion amendment's live-data shape was verified by reading the `dhanhq` SDK source directly (`process_full()`), not guessed, but not exercised against a live packet during market hours this session.
