# Debug Report — TASK-007

**Date:** 2026-07-03
**Verdict:** ✅ CLEAN

## What was run

- `python -m pytest tests/ -q` — full suite (146 passed, was 124 before this task), including the live `test_ingestion_service_end_to_end` (real Supabase + Dhan WS/REST, credentials present in `.env`)
- `python -m ruff check src/aeolus/signals src/aeolus/ingestion tests/signals tests/ingestion`
- `python -m mypy src/aeolus/signals src/aeolus/ingestion`

## Observed behavior

**TASK-002 amendment #3, human-approved before code, implemented:** `IngestionSnapshot` gained `expiry_date: date | None`. Zero new API calls — `IngestionService.start()` already resolved this into `self._option_expiry` via `OptionChainPoller.resolve_nearest_expiry()` (Dhan's `expiry_list` endpoint, confirmed against [the documented endpoint](https://docs.dhanhq.co/api/v2/option-chain/get-expiry-list)); `latest()` now threads it straight through. The live integration test (`test_service_integration.py`) actually ran this session (credentials present) and confirmed `snapshot.expiry_date is not None` against the real Dhan API — not just a unit-level assumption.

**Five pure functions built in `src/aeolus/signals/context.py`, per the approved ADR:**
- `dte` — plain date subtraction, explicitly exempted from the `SignalResult` contract (routing metadata for TASK-008's config selection, per OPEN_DECISIONS #2)
- `build_volume_price_histogram` + `value_area` — cycle-sampled volume-at-price approximation; POC = max-volume bucket, value area expands outward one heavier-neighbor bucket at a time until `area_pct` (0.70, human-confirmed) of volume is covered
- `prior_day_profile_shape` — `DayProfileShape.TREND`/`BALANCED` via range-expansion + close-location, returned alongside (not folded into) its `SignalResult`, polarity human-confirmed (TREND=GO, BALANCED=NO-GO)
- `gap_classification` — `no_gap`/`gap_and_go`/`gap_and_fill` vs yesterday's value area, `session_open` deliberately caller-captured (first tick of session) rather than the still-unverified Full-packet `open` field TASK-006 flagged
- `futures_basis_drift` — resolves OPEN_DECISIONS #4, reuses the confirm/diverge shape already established by `cvd_direction_and_divergence` (TASK-006)

**One real ADR gap closed during implementation, documented as an ADR amendment, not silently papered over:** `gap_classification`'s `no_gap` branch had no magnitude formula in the ADR's pseudocode (only the two beyond-VA branches did). Resolved with a fixed `sub_score = 0.45` (simplest reading of "weak NO-GO baseline") rather than inventing an unspecified proximity formula — flagged as a legitimate v2 refinement point, not an oversight.

## Issues

| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| — | — | None found | — | — |

## Constraint audit

- [x] No per-signal veto present — sub-scores only, `dte()` is routing metadata not a veto
- [x] No clock-time branching — verified programmatically (`test_no_function_takes_a_clock_argument`); `dte()` takes caller-supplied `date` objects (no internal `datetime.now()`/calendar read), `session_open` capture and cross-day state resets are explicitly the caller's job
- [x] Reason strings deterministic — reused `template_reason` stub; `context` param carries `close_location`/`value_area_width_ratio` (profile shape) and `price_trend` (basis drift)
- [x] Polarity check: GO favors option buying — profile-shape TREND/BALANCED test pair (human-confirmed 2026-07-03), gap-classification go/fill test pair, basis-drift confirm/diverge test pair (human-approved 2026-07-03 with the cost-of-carry-noise caveat noted)
- [x] `system_status` never feeds `market_state` — n/a, no concept of either here
- [x] `expiry_date` sourced from Dhan's own holiday-aware `expiry_list` resolution, never recomputed from an in-repo calendar — verified against `feed_rest.py`'s existing `resolve_nearest_expiry()` and the live test run this session
