# Architecture Decision Record — TASK-006

**Directive:** `directives/TASK-006_order-flow-signals.md`
**Status:** APPROVED (2026-07-03)
**Date:** 2026-07-03

## Problem

Compute Spec §6.4's three order-flow sub-signals — CVD build direction + divergence from price, delta imbalance/absorption at range extremes, session-relative volume-participation range — on the `SignalResult` contract from TASK-003/004/005. [OPEN_DECISIONS #1](../../docs/OPEN_DECISIONS.md) (volume-participation range) resolved 2026-07-03: include as specified.

**This module needs data `IngestionSnapshot` doesn't carry yet — bigger than the VIX/lot_size gap TASK-002 already amended once.** Checked before designing anything (not guessed): the `dhanhq` SDK's `process_full()` parser (the packet type already subscribed for the futures leg) unpacks `LTQ`, `volume`, `total_sell_quantity`, `total_buy_quantity`, `open`, `close`, `high`, `low` from every Full packet — **all six of these are already arriving over the wire and being silently discarded** by `feed_ws.py`'s current `_on_message`, which only extracts `LTP` and `depth`. No new WS subscription, no new segment, no new vendor — same packet, more fields parsed out of it. See Blocking Dependencies.

## Decision

Three pure functions in `src/aeolus/signals/order_flow.py`.

**1. `cvd_direction_and_divergence`:** Dhan gives per-strike/per-instrument OI directly but **no aggressor-tagged trade tape** — there's no way to know from this feed whether an individual print was buyer- or seller-initiated. True tick-by-tick CVD isn't computable. Approximated at cycle granularity instead, consistent with TASK-005's cycle-relative design: each cycle's volume increment is tick-rule-classified by futures LTP direction since the previous cycle.
```
volume_delta = current.volume - previous.volume        # exchange's own cumulative counter, see reconnect note below
cvd_delta = +volume_delta if current.futures_ltp > previous.futures_ltp
            -volume_delta if current.futures_ltp < previous.futures_ltp
            0              if unchanged (no attribution, not split 50/50 — avoids fabricating a lean on a flat tick)
```
Caller accumulates `cvd_delta_history: list[float]` (session-scoped, one entry per cycle, caller resets at session boundary — same "caller decides when a session starts, this module never checks a clock" boundary as every prior module) and a matching `price_history: list[float]` (futures LTP, same cycle indexing).

**Durability — checked against how [[ARGUS]] (sibling project, Obsidian) solves the same problem, then deliberately not copied wholesale.** ARGUS keeps CVD as an in-memory running float, persisted to its own `cvd_history` table as an append-only log, and resets on a **hardcoded 09:15 IST check** — that last part isn't portable here, this repo's CLAUDE.md bans clock-branching outside the scheduler. What *is* worth taking: an in-memory-only `cvd_delta_history` loses all continuity on a scheduler restart mid-session (crash, redeploy) — silently resets to an empty list with no signal that it happened. Fix doesn't need a new table: `signal_snapshots` (TASK-001, already written every cycle by TASK-008 with per-category raw readings) already persists CVD's raw value every cycle. So the contract is: on startup or restart, the caller (TASK-008/013) **seeds** `cvd_delta_history` from the current session's most recent `signal_snapshots` rows instead of starting cold at `[]`; on a genuine new session boundary (scheduler-detected, not clock-computed inside this module), it starts fresh. `order_flow.py` itself still does zero I/O — same discipline as every function in this ADR suite — this is entirely a caller-side contract, spelled out here so TASK-008/013's ADRs don't have to rediscover it.
```
cvd_trend = sum(cvd_delta_history)      # raw_value
price_trend = price_history[-1] - price_history[0]
confirming = same sign(price_trend, cvd_trend), price_trend beyond reference_band's flat-threshold
sub_score = 0.5 + 0.5 * _percentile_rank(abs(cvd_trend), trailing_cvd_magnitude_history)   if confirming
sub_score = 0.5 - 0.5 * _percentile_rank(abs(cvd_trend), trailing_cvd_magnitude_history)   if diverging (price moved, CVD didn't agree)
sub_score = 0.5                                                                             if price_trend is flat (nothing to confirm or diverge from)
```
Directly implements the spec's own framing: "price progress without CVD confirmation = fragile move" → diverging scores low (NO-GO-favorable), confirming scores high (GO-favorable). Same `0.5 ± 0.5·percentile` shape as TASK-004's `gex_regime` and TASK-005's `pcr_level_and_roc` — third reuse of this pattern, worth naming as the de facto standard shape for "does X confirm or contradict Y" signals across this signal suite. `reference_band` binds to `|price_trend|` (the confirm/flat gate), not `cvd_trend` — same explicit reference_band-vs-raw_value split called out in TASK-005's PCR function, for the same reason (constraint #3 wants the number that gated the decision, not a mismatched one).

**CVD reset-across-reconnect (directive's named edge case) — resolved by construction, not handled specially:** `volume_delta` is computed from Dhan's own exchange-side cumulative session counter (`current.volume - previous.volume`), never from a running total this module or `feed_ws.py` maintains itself. A WS reconnect doesn't reset the exchange's counter — the next Full packet after reconnect simply carries the true cumulative volume as of that moment. A reconnect gap produces one larger-than-usual `volume_delta` for the cycle spanning the gap, not a double-count and not a silent reset to zero. No special-case code needed; flagging so it's clear this was a design choice, not an oversight.

**2. `delta_imbalance_and_absorption`:**
```
imbalance = (total_buy_quantity - total_sell_quantity) / (total_buy_quantity + total_sell_quantity)   # -1..1, raw_value
range_span = current.day_high - current.day_low
near_low  = (current.futures_ltp - current.day_low)  / range_span < extreme_threshold
near_high = (current.day_high - current.futures_ltp) / range_span < extreme_threshold
confirms_extreme = (near_low and imbalance < 0) or (near_high and imbalance > 0)   # sell-heavy at the low / buy-heavy at the high -> extreme likely breaks
opposes_extreme  = (near_low and imbalance > 0) or (near_high and imbalance < 0)   # buy-heavy at the low / sell-heavy at the high -> absorption, extreme likely holds
```
**Polarity — flagging as the riskiest call in this module, more subjective than TASK-004's GEX sign or TASK-003's IV-RV spread:** `confirms_extreme` → `sub_score = 0.5 + 0.5 * magnitude_pct` (GO-favorable — the imbalance agrees with a break, movement developing). `opposes_extreme` (absorption) → `sub_score = 0.5 - 0.5 * magnitude_pct` (NO-GO-favorable — resting orders are defending the extreme, range likely holds, reinforces "pinned"). This reading treats absorption the same way TASK-004 treats a strong pin-magnet (near the flip level) and TASK-005 treats short covering/long unwinding — a force acting *against* a developing move gets scored NO-GO. **The counter-argument, worth the human weighing explicitly:** absorption is itself a genuine, information-dense order-flow event (real capital defending a level) — an equally defensible reading is that *any* strong imbalance at an extreme is GO-favorable regardless of which side, because something is clearly happening rather than nothing. Not near either extreme → weaker baseline lean (`0.5 + 0.25 * magnitude_pct`, imbalance alone is still mildly informative mid-range but capped below the at-extreme conviction ceiling). `magnitude_pct = _percentile_rank(abs(imbalance), trailing_imbalance_magnitude_history)`, same shared helper, fourth reuse.

**Polarity confirmed as designed (human sign-off, 2026-07-03), with a reversal question resolved explicitly rather than special-cased.** Human's framing matched the design's intent directly: *"during absorption phase, price doesn't move that much leading to premium decay"* — confirms absorption = NO-GO at the moment it's observed. The follow-up question — what about a reversal that starts once aggressive absorption resolves? — is **not** handled inside this function, on purpose: `delta_imbalance_and_absorption` reads the current cycle only, it has no forward-looking claim about whether *this* absorption event will produce a reversal (that's a prediction, not a reading, and AEOLUS's whole design premise — Spec's "weather app, not a signal generator" — is to re-forecast every cycle rather than front-run outcomes). If an absorption event *does* resolve into a genuine reversal, the reversal shows up as a **new, later-cycle reading**: price starts actually moving away from the extreme, `cvd_direction_and_divergence` picks up confirming CVD, and this same function's `imbalance` naturally flips from `opposes_extreme` to `confirms_extreme` (of the *new* direction) once order flow shifts — the system re-scores GO on its own, no special-casing needed. Whether strong/aggressive absorption specifically predicts reversals more often than weak absorption is a legitimate v2 question — but it's an empirical one, answerable once `outcome_labels` (Spec §11) has enough history to check, not a hand-tuned guess to bake in now. Noted here so it isn't lost, deliberately not implemented.

**Volume-participation threshold — `volume_participation_pct = 0.10` (human-confirmed default, 2026-07-03).** Passed as a caller-supplied parameter (not hardcoded inside `order_flow.py`, same "config-sourced, not module-constant" discipline as every reference_band in this ADR suite) so it can be recalibrated from `config/` without a code change once real volume-curve data exists to check it against.

**3. `volume_participation_range`:** Dhan's `volume` field is the exchange's own cumulative-since-session-start counter (confirmed from the packet struct, resets server-side each trading day — this module never resets it itself, same as function 1's reconnect argument). The range-establishment moment is detected, not separately tracked tick-by-tick:
```
threshold_volume = volume_participation_pct * average_daily_volume   # X% of a trailing-average reference, NOT today's own (unknowable-in-real-time) total
if established_range is None and current.volume >= threshold_volume:
    established_range = (current.day_low, current.day_high)   # snapshot AT the crossing moment — day_low/day_high up to now IS the participation-window range, since nothing after this moment has happened yet
```
Once established, held by the caller (same session-scoped-state discipline as `cvd_delta_history` and TASK-005's `session_open_max_pain`) and passed back in each cycle — this function never re-derives it once set.
```
if inside established_range: raw_value = 0
else: raw_value = signed distance beyond the nearer edge (points)
sub_score = 0.5 - 0.5 * inside_pct   OR   0.5 + 0.5 * _percentile_rank(|excursion|, trailing_excursion_history)  -- see below
```
Holding inside = compression, no breakout yet = NO-GO-favorable (consistent with the "quiet = NO-GO" theme running through every module so far). Breaking outside = GO-favorable, magnitude-scaled the same way as functions 1/2. **This function deliberately does not try to detect multi-cycle "holding" outside the range** (a single-cycle poke vs. a sustained break) — the spec's own hysteresis/debounce requirement (mandatory system-wide, `docs/CONSTRAINTS.md`) already exists precisely to prevent single-cycle noise from flipping state, so re-implementing a "did it hold" check here would duplicate that layer. This function reports the instantaneous reading every cycle; persistence is TASK-008's job.

**Degenerate-range edge case (directive's named edge case):** low volume at the open could cross `threshold_volume` on cycle 1 with almost no real price discovery, freezing a near-zero-width range that then "breaks" on every subsequent tick. Mitigation: `established_range` is only set if `current.day_high - current.day_low` also exceeds a caller-supplied `min_range_width`, else the function keeps waiting past the volume threshold until a non-degenerate range exists. Flagging the floor value itself as a calibration knob, not picking one here.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/signals/order_flow.py` | Three sub-signal functions + private helpers (`_tick_classify`, `_near_extreme`) |

## API Contracts

```python
# src/aeolus/signals/order_flow.py
def cvd_direction_and_divergence(
    cvd_delta_history: list[float],
    price_history: list[float],
    trailing_cvd_magnitude_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult: ...

def delta_imbalance_and_absorption(
    current: IngestionSnapshot,   # needs total_buy_quantity/total_sell_quantity/day_high/day_low — see Blocking Dependencies
    trailing_imbalance_magnitude_history: list[float],
    extreme_threshold: float,
    reference_band: tuple[float, float],
) -> SignalResult: ...

def volume_participation_range(
    current: IngestionSnapshot,   # needs volume/day_high/day_low
    established_range: tuple[float, float] | None,
    average_daily_volume: float,
    volume_participation_pct: float,
    min_range_width: float,
    trailing_excursion_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult: ...
```

## Blocking Dependencies — RESOLVED 2026-07-03 (except #2, informational only)

1. **RESOLVED — TASK-002 amendment #2, larger than #1 (VIX/lot_size):** human approved adding `volume: int | None`, `total_buy_quantity: int | None`, `total_sell_quantity: int | None`, `day_high: float | None`, `day_low: float | None` to `IngestionSnapshot`. Implemented: `feed_ws.py`'s `_on_message` now parses these five from the futures `Full` packet (`volume`, `total_buy_quantity`, `total_sell_quantity`, `high`, `low` — the SDK's own field names; `high`/`low` map to `day_high`/`day_low`, not the unrelated `oi_day_high`/`oi_day_low` fields in the same packet, which track OI extremes not price). No new WS subscription, segment, or vendor — same packet already subscribed for the futures leg since TASK-002.
2. **`close`/`open` fields in the Full packet need live verification before use, not assumed:** the SDK's `process_full()` also unpacks `open`/`close`, but in a mid-session live feed "close" conventionally means *previous day's* close (there's a separate `process_prev_close` packet type in the same SDK, suggesting possible redundancy or a different meaning) — not verified against a live packet this session. Not used by any function above; flagging so a future module doesn't assume "today's close" without checking first.
3. **Absorption polarity (function 2)** — see the flagged paragraph above. Needs an explicit human call, not just a design-doc default.
4. **`average_daily_volume` sourcing** — same caller-owns-history-fetch pattern as every prior module's trailing lists (not this module's job to query), but flagging explicitly since it's a new *kind* of external input (a single trailing average, not a list) — confirm the scheduler/composite layer's ADR (TASK-008/013) will supply it.

## Implementation Amendment (2026-07-03) — two gaps closed while writing code

**1. `volume_participation_range`'s `established_range` return channel.** The Decision section's pseudocode computes `established_range` as a local value inside the function, but the API Contract only returns a `SignalResult` 4-tuple — there's no field for handing the newly-established `(day_low, day_high)` back to the caller for it to hold and pass in next cycle. Resolved by surfacing the active `established_range` bounds (whether just-established this cycle or already held) via `template_reason`'s `context` param every cycle: `context={"established_low": low, "established_high": high}`. This reuses the existing "extra numbers for explainability" channel for a second, load-bearing purpose (caller-side state persistence) — a stretch of `context`'s original intent, but the only channel available without breaking the `SignalResult` contract every other module in this suite depends on. Flagging for TASK-008/013's ADR to either formalize this contract explicitly or introduce a proper mechanism if this proves awkward in practice.

**2. `cvd_direction_and_divergence`'s flat-threshold source.** The Decision section says "`price_trend` beyond `reference_band`'s flat-threshold" without specifying which bound. Implemented as `reference_band[0]` (the low bound) — `abs(price_trend) < reference_band[0]` is the flat case (`sub_score = 0.5`), matching the general convention elsewhere in this ADR suite that a band's low value marks a floor/threshold (e.g. TASK-003's `expected_move_consumed_ratio` band low). The high bound is unused in this function's computation, same as most `reference_band`s in this suite (TASK-004/005 use theirs purely as pass-through for the reason string / TASK-008's interpretation, not as a scoring input).

## Performance / Failure Modes

- **No caching or internal state** — same discipline as every prior module in this suite; `established_range`, `cvd_delta_history`, etc. all live in the caller.
- **`total_buy_quantity + total_sell_quantity == 0`:** `delta_imbalance_and_absorption` returns `(None, band, 0.5, reason)` rather than dividing by zero — genuinely no book to read.
- **`day_high == day_low`** (first tick of the session, or a completely flat market): `_near_extreme` and `volume_participation_range`'s range-span math both guard against zero-width division the same way, degrading to the insufficient-data path.

## Definition of Done

- [x] Integration-style tests calling all three public functions directly with realistic `IngestionSnapshot`-shaped inputs — no mocking of internals
- [x] CVD tick-classification test: constructed price-up/price-down/price-flat sequences produce the documented signed/zero attribution
- [x] CVD-divergence polarity test: price trending one way, CVD trend disagreeing, confirms `sub_score` drops toward NO-GO — and the reverse (confirming) case scores toward GO
- [x] Absorption polarity test per the flagged formula: confirms-extreme vs opposes-extreme produce opposite-direction `sub_score` shifts
- [x] Degenerate-range test: low `average_daily_volume`/early-session low volume does not freeze a sub-`min_range_width` range
- [x] Zero-total-buy-sell-quantity test: `delta_imbalance_and_absorption` returns the explicit `None`/`0.5` path, no `ZeroDivisionError`
- [x] Constraint check: no per-signal veto (n/a — sub-scores only), no clock-time branching (session-boundary detection for `cvd_delta_history` reset is explicitly the caller's job, never this module's), deterministic reasons (via TASK-010 stub), polarity correct (flagged absorption call needs explicit sign-off, not just a passing test) — human-confirmed 2026-07-03

**Implemented:** 2026-07-03. See `reports/debug/TASK-006_debug-report.md`, `reports/qa/TASK-006_qa-report.md`.
