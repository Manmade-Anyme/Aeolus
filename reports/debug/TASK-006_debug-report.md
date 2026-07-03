# Debug Report — TASK-006

**Date:** 2026-07-03
**Verdict:** ✅ CLEAN

## What was run

- `python -m pytest tests/ -q` — full suite (124 passed, was 96 before this task)
- `python -m ruff check src/aeolus/signals src/aeolus/ingestion tests/signals tests/ingestion`
- `python -m mypy src/aeolus/signals src/aeolus/ingestion`

## Observed behavior

**Blocking-dependency resolution before any code was written:** this ADR's Blocking
Dependencies section asked for explicit human sign-off on a TASK-002 ingestion amendment
larger than the earlier VIX/lot_size one — `IngestionSnapshot` needed `volume`,
`total_buy_quantity`, `total_sell_quantity`, `day_high`, `day_low`, all five already arriving
in the futures `Full` packet but previously unparsed by `feed_ws.py`. Asked the human directly;
approved. Amended `src/aeolus/ingestion/models.py` (5 new fields), `feed_ws.py` (`_on_message`
now parses `volume`/`total_buy_quantity`/`total_sell_quantity`/`high`/`low` from the Full
packet — verified against the `dhanhq` SDK's own `process_full()` source to confirm exact
field names, not guessed; note `high`/`low` map to `day_high`/`day_low`, distinct from the same
packet's `oi_day_high`/`oi_day_low`, which track OI extremes, not price), and `service.py`
(wires the five new `LiveFeed` getters into `IngestionSnapshot`). Existing tests
(`test_models.py`, `test_service_integration.py`, TASK-005's `test_oi_structure_integration.py`)
updated for the new required-but-nullable fields; new `test_feed_ws_parsing.py` added — unit-level
parsing tests using a synthetic Full packet, no live market hours needed.

**Two real ADR gaps closed during implementation, not guessed silently:**
1. `volume_participation_range`'s `established_range` had no return channel in the `SignalResult`
   4-tuple despite the ADR's pseudocode implying the function computes it. Resolved by surfacing
   the active `established_low`/`established_high` via `template_reason`'s `context` param every
   cycle — documented as an ADR amendment, flagged for TASK-008/013 to formalize.
2. `cvd_direction_and_divergence`'s flat-threshold source (which `reference_band` bound gates
   `price_trend`) was underspecified — implemented as `reference_band[0]`, matching the
   low-bound-as-floor convention used elsewhere in this ADR suite.

## Issues

| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| — | — | None found | — | — |

## Constraint audit

- [x] No per-signal veto present — sub-scores only, no state decision here
- [x] No clock-time branching in signal logic — verified programmatically (`test_no_function_takes_a_clock_or_datetime_argument`); session-boundary detection for `cvd_delta_history`/`established_range` resets is explicitly the caller's job, never this module's
- [x] Reason strings deterministic — reused `template_reason` stub, `context` param carries `price_trend` (CVD) and `established_low`/`established_high` (volume-participation range)
- [x] Polarity check: GO favors option buying — CVD confirm/diverge test pair, absorption confirms/opposes test pair (human-confirmed polarity, 2026-07-03: "during absorption phase, price doesn't move much leading to premium decay" → NO-GO), volume-participation inside/breakout test pair
- [x] `system_status` never feeds `market_state` — n/a, no concept of either here
- [x] CVD reset-across-reconnect handled by construction — `volume_delta` derives from Dhan's own exchange-side cumulative counter (`current.volume - previous.volume`), never a running total this module maintains; a WS reconnect produces one larger `volume_delta` for the spanning cycle, not a double-count or silent reset
