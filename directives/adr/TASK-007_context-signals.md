# Architecture Decision Record — TASK-007

**Directive:** `directives/TASK-007_context-signals.md`
**Status:** APPROVED (2026-07-03)
**Date:** 2026-07-03

## Problem

Compute Spec §6.5's three context items on the `IngestionSnapshot`/`SignalResult` contracts established by TASK-003..006: yesterday's completed profile shape (trend vs balanced/rotational), gap type at open vs yesterday's value area, DTE relative to the Tuesday-anchored NSE expiry.

**Two gaps found by checking the actual data path before designing (same discipline TASK-006 used), not assumed from the directive's wording:**

1. **DTE doesn't need a holiday calendar re-implemented here — Dhan already resolves it.** [`OptionChainPoller.resolve_nearest_expiry()`](../../src/aeolus/ingestion/feed_rest.py) calls Dhan's `expiry_list` endpoint, which is already holiday-shift aware (its own docstring: *"Expiry is sourced from Dhan's own expiry_list endpoint rather than recomputed from the NSE holiday calendar here"*). [`IngestionService.start()`](../../src/aeolus/ingestion/service.py:46) resolves this once into `self._option_expiry: date`, **but never surfaces it on `IngestionSnapshot`** — `latest()` builds the snapshot from thirteen fields and `_option_expiry` isn't one of them. The directive's "Tuesday anchor, NSE holiday calendar, never hardcoded weekday" acceptance criterion is satisfied for free by consuming this already-resolved date, not by this module owning any calendar logic. See Blocking Dependencies #1.
2. **No per-price volume histogram exists anywhere in the feed.** `IngestionSnapshot` gives per-cycle `futures_ltp`, `volume` (session cumulative), `day_high`/`day_low` — no volume-at-price distribution, so "value area" (a market-profile concept — the price band holding ~70% of a session's volume) has no ready-made source. Also: TASK-006's ADR already flagged the Full packet's `open`/`close` fields as **unverified/ambiguous** (possible prev-close confusion via the SDK's separate `process_prev_close` packet type) — this module must not lean on them for "today's open."

**Human-confirmed 2026-07-03:** build a true cycle-sampled volume-at-price histogram (approximating market profile at cycle granularity, same approximation strategy TASK-006 used for CVD) rather than substituting a cheaper range/close-location-only proxy for value area.

This ADR also resolves [OPEN_DECISIONS #4](../../docs/OPEN_DECISIONS.md)'s scoping: `futures_basis` session-drift interpretation lands here (function 5, below).

## Decision

Five functions in `src/aeolus/signals/context.py`, plus a `DayProfileShape` enum. Per Open Decision #2's resolution (DTE routes config selection, not composite scoring), **`dte()` is explicitly exempted from the standard `(raw_value, reference_band, sub_score, reason_string)` contract** — it is routing metadata consumed directly by TASK-008/009, not a scored sub-signal. Only `prior_day_profile_shape` and `gap_classification` follow the standard contract; this narrows the directive's "standard contract per sub-signal" criterion to the two functions that are actually signals, and is called out explicitly because it reads as a deviation from the directive's literal wording.

**1. `dte(session_date, expiry_date)`:**
```python
dte = (expiry_date - session_date).days   if expiry_date is not None else None
```
Pure subtraction against a date this module receives as input — no calendar, no weekday math, no clock read. `expiry_date` comes from `IngestionSnapshot` once Blocking Dependency #1 lands. `None` in → `None` out (e.g. mid-startup before `IngestionService.start()` has resolved anything) rather than raising — same "explicit don't-know, never guessed" discipline as every optional `IngestionSnapshot` field.

**2. `build_volume_price_histogram` + `value_area` (private-facing helpers, one exported pair):**
```python
histogram: dict[float, float] = {}
for price, volume_delta in cycle_price_volume_history:     # (futures_ltp, volume_delta) per cycle
    bucket = round(price / bucket_size) * bucket_size
    histogram[bucket] = histogram.get(bucket, 0.0) + volume_delta
```
`cycle_price_volume_history` is caller-held, session-scoped, growing by one `(futures_ltp, volume_delta)` tuple per cycle — same shape and same caller-owns-the-list discipline as TASK-006's `cvd_delta_history`/`price_history` (this module does zero I/O and holds zero state itself, consistent with every function in this ADR suite so far). `volume_delta = current.volume - previous.volume`, reusing TASK-006's already-established reasoning: it's the exchange's own cumulative counter, so a WS reconnect mid-session produces one oversized bucket contribution for that cycle, not a double-count or reset.

`value_area(histogram, area_pct)`: POC = the bucket with max accumulated volume; value area expands outward from POC, at each step adding whichever adjacent bucket (above or below the current band) holds more volume, until cumulative share ≥ `area_pct` of total. Returns `(poc, va_low, va_high)`, or `None` if the histogram is empty (no prior-day data yet — see edge cases). **`area_pct = 0.70` — human-confirmed (2026-07-03), standard market-profile convention, locked as the default (not just proposed).**

**Why cycle-granularity, not tick-granularity:** identical tradeoff TASK-006 accepted for CVD — Dhan's feed doesn't give a trade tape, and this system samples at computation-cycle cadence, not tick cadence. A cycle-sampled histogram is an approximation of true market profile (which would bucket every print), not the real thing; flagging so nobody mistakes `value_area`'s output for a TPO-chart-accurate figure.

**3. `prior_day_profile_shape`:**
```python
day_range = prior_day_high - prior_day_low
range_expansion = day_range / trailing_average_range           # trailing_average_range: caller-supplied, same pattern as TASK-006's average_daily_volume
close_location = (prior_close - prior_day_low) / day_range      # 0 = closed at day's low, 1 = closed at day's high
value_area_width_ratio = (va_high - va_low) / day_range         # secondary, reinforcing signal — a trend day tends to leave a narrow, one-sided value area relative to its range

is_trend_day = range_expansion > expansion_threshold and (
    close_location > extreme_threshold or close_location < (1 - extreme_threshold)
)
```
**Rationale:** without true TPO data (multiple overlapping time-price-opportunity columns), "trend day" is approximated as *"range expanded beyond the recent norm AND the session closed near an extreme rather than reverting toward the middle"* — one directional push that held into the close, vs. a session that oscillated and closed mid-range (balanced/rotational). `value_area_width_ratio` is computed and passed through the reason string as a reinforcing data point, not a second gating condition — avoids stacking two independent thresholds into one binary flag, keeping the trend/balanced call auditable from a single named formula.

**Polarity — human-confirmed (2026-07-03), same bar as TASK-006's absorption call:** `DayProfileShape.TREND` → GO-favorable (`sub_score = 0.5 + 0.5 * percentile_rank`), `DayProfileShape.BALANCED` → NO-GO-favorable (`sub_score = 0.5 - 0.5 * percentile_rank`), consistent with the "quiet/pinned = NO-GO, movement = GO" theme running through every module in this suite (GEX regime, PCR level, volume-participation breaks). **The counter-argument, worth weighing explicitly:** yesterday being a trend day could just as easily mean the move is already extended and due to mean-revert today — this function makes no forward claim about *today*, it only classifies *yesterday's completed shape* as a fact for TASK-009's outlook and this category's sub-score; whether "yesterday trended" empirically predicts "today continues" vs "today reverts" is exactly the kind of question `outcome_labels` (Spec §11) can answer once history accumulates — not guessed here.

`DayProfileShape` is returned **alongside**, not folded into, the `SignalResult` — `tuple[DayProfileShape | None, SignalResult]` — per the directive's explicit requirement that TASK-009 gets a standalone flag, never something it has to reverse-engineer out of a blended sub-score.

**4. `gap_classification`:**
```python
gap_direction = session_open - prior_close
gapped_beyond_va = session_open > prior_va_high or session_open < prior_va_low   # "gap" means outside yesterday's value area, not just != prior_close

if not gapped_beyond_va:
    classification = "no_gap"     # opened back inside yesterday's equilibrium zone — weak/neutral read
elif same_side(current_futures_ltp - session_open, gap_direction):
    classification = "gap_and_go"     # price continuing further away from the VA in the gap's direction
else:
    classification = "gap_and_fill"   # price already reverting back toward/into yesterday's VA
```
`session_open` is **not** read from the Full packet's `open` field (unverified/ambiguous per TASK-006's flag #2) — it is the caller-captured first non-`None` `futures_ltp` observed each session, same session-scoped-state discipline as `cvd_delta_history` (reset only on a scheduler-detected session boundary, never a clock check inside this module). This function is stateless per call otherwise: it re-derives its classification fresh every cycle from `session_open` (fixed for the session) and `current_futures_ltp` (this cycle's reading) rather than remembering "did it fill last cycle" — same reasoning TASK-006 used to justify *not* tracking multi-cycle "holding" inside `volume_participation_range`: hysteresis over consecutive cycles is TASK-008's job, this function reports the instantaneous read.

**Polarity:** `gap_and_go` → GO-favorable (continuation, movement developing) — same shape as every other confirming/diverging signal in this suite. `gap_and_fill` → NO-GO-favorable (reversion, the gap's information content is being erased). `no_gap` → weak NO-GO baseline (`sub_score` capped below the conviction ceiling, same treatment TASK-006 gives a mid-range imbalance reading) — opening inside yesterday's equilibrium zone carries no fresh directional information. `raw_value = gap_direction` (points beyond the nearer VA edge, 0 if `no_gap`); `reference_band` binds to the VA edges, same explicit raw-value-vs-reference-band split as every prior module.

**5. `futures_basis_drift`** — scopes [OPEN_DECISIONS #4](../../docs/OPEN_DECISIONS.md), which explicitly deferred "session-drift interpretation of `futures_basis`" to whichever module's ADR picked it up, naming TASK-007 as most likely. `futures_basis` (`futures_ltp − spot_ltp`) has been on `IngestionSnapshot` since TASK-002 — no new dependency, just the first module to actually read it.
```python
basis_trend = basis_history[-1] - basis_history[0]     # drift since session open, caller-held session-scoped list, same discipline as cvd_delta_history
```
**Design choice: reuse the confirm/diverge shape already established three times over (CVD vs price, PCR level/RoC, GEX regime), rather than inventing new basis-specific semantics.** Widening basis *while price also trends* reads as confirmation (increasing futures premium tracking a directional move — leveraged positioning building in the same direction); basis moving opposite to the price trend reads as divergence (positioning not backing the move — same "fragile move" tell as CVD divergence). This is deliberately the same reasoning as `cvd_direction_and_divergence`, not a fresh theory of what basis means, because the spec gives no independent framing for it (v2-deferred item pulled forward by Open Decision #4, not a Section 6.5 bullet) — reusing a pattern already reasoned through and human-confirmed elsewhere is lower-risk than inventing a fifth polarity call in one module.
```python
confirming = same_sign(price_trend, basis_trend), |price_trend| beyond reference_band's flat-threshold
sub_score = 0.5 + 0.5 * percentile_rank(|basis_trend|, trailing_basis_magnitude_history)   if confirming
sub_score = 0.5 - 0.5 * percentile_rank(|basis_trend|, trailing_basis_magnitude_history)   if diverging
sub_score = 0.5                                                                             if price_trend flat
```
**Human-confirmed 2026-07-03, with the caveat flagged and accepted, not waved through:** approved to proceed on this reuse despite the "could be pure cost-of-carry noise" counter-argument above — worth re-checking against `outcome_labels` once history accumulates (same v2 empirical-check path as the absorption-reversal question in TASK-006).

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/signals/context.py` | `dte`, `build_volume_price_histogram`, `value_area`, `prior_day_profile_shape`, `gap_classification`, `futures_basis_drift`, `DayProfileShape` enum |

## API Contracts

```python
# src/aeolus/signals/context.py
from datetime import date
from enum import Enum

class DayProfileShape(str, Enum):
    TREND = "trend"
    BALANCED = "balanced"

def dte(session_date: date, expiry_date: date | None) -> int | None: ...

def build_volume_price_histogram(
    cycle_price_volume_history: list[tuple[float, float]],   # (futures_ltp, volume_delta) per cycle, session-scoped, caller-held
    bucket_size: float,
) -> dict[float, float]: ...

def value_area(
    histogram: dict[float, float],
    area_pct: float,
) -> tuple[float, float, float] | None: ...   # (poc, va_low, va_high); None if histogram empty

def prior_day_profile_shape(
    prior_day_high: float | None,
    prior_day_low: float | None,
    prior_close: float | None,
    prior_value_area: tuple[float, float, float] | None,     # (poc, va_low, va_high), caller-seeded from last session_date's snapshot
    trailing_average_range_history: list[float],
    expansion_threshold: float,
    extreme_threshold: float,
    reference_band: tuple[float, float],
) -> tuple[DayProfileShape | None, SignalResult]: ...

def gap_classification(
    session_open: float | None,          # caller-captured: first non-None futures_ltp this session
    current_futures_ltp: float | None,
    prior_value_area: tuple[float, float, float] | None,
    prior_close: float | None,
    trailing_gap_magnitude_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult: ...

def futures_basis_drift(
    basis_history: list[float],          # futures_basis per cycle, session-scoped, caller-held
    price_history: list[float],          # futures_ltp per cycle, same indexing
    trailing_basis_magnitude_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult: ...
```

## Blocking Dependencies — RESOLVED 2026-07-03

1. **RESOLVED — TASK-002 amendment #3, human-approved, source confirmed:** expose `expiry_date: date | None` on `IngestionSnapshot`. Confirmed source is Dhan's [`/v2/optionchain/expirylist`](https://docs.dhanhq.co/api/v2/option-chain/get-expiry-list) endpoint — the same one `OptionChainPoller.resolve_nearest_expiry()` already calls. Zero new API calls — `IngestionService.start()` already resolves this into `self._option_expiry`; `latest()` just needs to thread it through into the constructed `IngestionSnapshot`.
2. **RESOLVED — `session_open` sourcing approved:** deliberately *not* an `IngestionSnapshot` field. Caller (TASK-008/013) captures the first non-`None` `futures_ltp` of each session as session-scoped state, same discipline as `cvd_delta_history`. Sidesteps TASK-006's flagged ambiguity around the Full packet's `open` field entirely, rather than resolving that ambiguity first.
3. **Cross-session persistence** — `prior_day_high`, `prior_day_low`, `prior_close`, `prior_value_area`, `trailing_average_range_history`, `trailing_gap_magnitude_history` all need to survive the day boundary. Reuses the exact pattern TASK-006 established for `cvd_delta_history`: caller seeds these from the previous session_date's final `signal_snapshots` row rather than a dedicated table. Concretely, whatever writes `signal_snapshots` (TASK-008) must persist this module's `context` category raw_readings at each cycle's end (`poc`, `va_low`, `va_high`, `day_high`, `day_low`, `close`) so the next session_date's first cycle can seed from them — flagging so TASK-008/013's ADRs pick this up rather than rediscovering it, same as TASK-006 did for its own seeding contract.
4. **Calibration knobs — `area_pct` RESOLVED (0.70, locked), rest still open, config-sourced not module constants (same discipline as every `reference_band`/threshold in this suite):** `bucket_size` (NIFTY point increment for histogram bucketing), `expansion_threshold`, `extreme_threshold` — tuned during implementation, recorded in the debug report, not picked here.
5. **RESOLVED — Profile-shape polarity (trend=GO, balanced=NO-GO), human-confirmed.** See flagged paragraph above.
6. **RESOLVED — `futures_basis_drift`'s confirm/diverge reuse, human-approved with caveat acknowledged.** Resolves [OPEN_DECISIONS #4](../../docs/OPEN_DECISIONS.md) by scoping the session-drift interpretation here. No ingestion dependency (`futures_basis` already on `IngestionSnapshot` since TASK-002).

## Performance / Failure Modes

- **No caching or internal state** — `context.py` holds nothing itself; `cycle_price_volume_history`, `session_open`, all `prior_*` inputs live in the caller, same discipline as every prior module.
- **First session after go-live (directive's named edge case):** all `prior_*` inputs are `None`/empty. `value_area` returns `None` on an empty histogram; `prior_day_profile_shape` returns `(None, SignalResult(None, band, 0.5, reason="no prior-day baseline yet"))`; `gap_classification` degrades the same way if `prior_value_area` is `None`.
- **`prior_day_high == prior_day_low`** (degenerate/flat prior session): `day_range` guarded against zero-width division before computing `range_expansion`/`close_location`, same guard style as TASK-006's `_near_extreme`.
- **Gap classification before value area is computable** (directive's named edge case): covered by the same `prior_value_area is None` insufficient-data path above — there is no separate "computing" state, the value area is either seeded (prior session complete) or absent (first day).

## Implementation Amendment (2026-07-03) — one gap closed while writing code

**`gap_classification`'s `no_gap` branch had no magnitude formula in the Decision section's pseudocode** — only the two beyond-VA branches (`gap_and_go`/`gap_and_fill`) had a magnitude-scaled `sub_score`. Resolved with the simplest reading of "weak NO-GO baseline, capped below the conviction ceiling": a fixed `sub_score = 0.45` (not magnitude-scaled, since `raw_value = 0.0` in this branch gives no magnitude to scale against) rather than inventing a proximity-to-value-area-center formula the ADR never specified. Flagging so this isn't mistaken for an oversight — a magnitude-scaled version (e.g. distance from the value area's midpoint) is a legitimate v2 refinement if `0.45` proves too coarse in practice.

## Definition of Done

- [x] Integration-style tests calling all five public functions directly with realistic inputs — no mocking of internals
- [x] `dte()` test: correct subtraction, `None` expiry → `None` out, zero/negative DTE (expiry day itself, or a stale past expiry) handled without raising
- [x] Histogram/value-area test: synthetic multi-bucket distribution produces the expected POC and a value area that actually holds ≥ `area_pct` of total volume
- [x] Trend-vs-balanced test: constructed range-expansion + close-location cases produce the documented `DayProfileShape` and matching `sub_score` direction (and the reverse case)
- [x] Gap-classification test: all three classifications (`no_gap`/`gap_and_go`/`gap_and_fill`) produce the documented polarity, plus the missing-input insufficient-data path
- [x] Zero-range guard test: `prior_day_high == prior_day_low` does not raise `ZeroDivisionError`
- [x] `futures_basis_drift` confirm/diverge test: basis trend agreeing vs disagreeing with price trend produces opposite-direction `sub_score` shifts, flat-price case returns `0.5`
- [x] Constraint check: no per-signal veto (n/a — sub-scores only), no clock-time branching (`session_open` capture and cross-day reset are explicitly the caller's job, never this module's), deterministic reasons (via TASK-010 stub), polarity correct (flagged profile-shape and basis-drift-reuse calls confirmed by human, 2026-07-03)

**Implemented:** 2026-07-03. See `reports/debug/TASK-007_debug-report.md`, `reports/qa/TASK-007_qa-report.md`.
