# Architecture Decision Record — TASK-005

**Directive:** `directives/TASK-005_oi-structure-signals.md`
**Status:** APPROVED (2026-07-03)
**Date:** 2026-07-03

## Problem

Compute Spec §6.3's four OI-structure sub-signals — PCR level + ROC, per-strike OI buildup classification, OI wall proximity + strength/decay, max-pain drift over the session — on the `SignalResult` contract from [TASK-003](TASK-003_volatility-signals.md)/[TASK-004](TASK-004_gamma-signals.md). Unlike those two modules, three of these four signals are inherently **cycle-relative** (need "what changed since last time"), not just statistically-relative to a long trailing window — the directive explicitly requires the snapshot interval this depends on to be nailed down here.

## Decision

**Snapshot-interval decision (directive's explicit ask):** "previous" means **the immediately preceding computation cycle's `IngestionSnapshot`**, whatever cadence the scheduler (TASK-013) actually runs at — never a fixed wall-clock duration like "an hour ago" computed by this module. This module receives `current: IngestionSnapshot, previous: IngestionSnapshot | None` from its caller each cycle; it does not fetch, cache, or reconstruct history itself, same DB/state boundary TASK-003/004 already established. This is a deliberate shape difference from TASK-003/004's `list[float]` trailing-history params: those needed a statistical distribution over many sessions, these need exactly one prior data point (plus, for max-pain drift, one session-anchored reference point) — different data need, not inconsistent design.

Four pure functions in `src/aeolus/signals/oi_structure.py`, all still stateless as functions (no internal memory) — statefulness lives entirely in what the caller passes in, same discipline as TASK-003/004.

**1. `pcr_level_and_roc`:** `raw_value` is the **rate of change** (`pcr_now - pcr_prev`), not the level — spec's own framing ("a static 1.1 tells you less than one that moved 0.9→1.2") says ROC carries the actionable information, so it's what `reference_band` and `sub_score` are built from, keeping constraint #3's "traces to a specific number crossing a specific threshold" honest (the number that mattered for scoring is the number reported as `raw_value`). Current level is still real information — surfaced via the `template_reason` `context` amendment above, not silently dropped. `sub_score = 0.5 + 0.5 * _percentile_rank(abs(roc), trailing_pcr_roc_magnitude_history)`: flat PCR → neutral `0.5`; fast-moving PCR (either direction) → toward `1.0`. **Direction-agnostic on purpose** — spec doesn't say a rising PCR is more GO-favorable than a falling one, only that *movement itself* is informative (repositioning happening) vs a static reading (complacency). First cycle of the day (`previous is None`) → `(None, band, 0.5, reason)`.

**2. `oi_buildup_classification`** *(updated by the Blocking Dependencies resolution below — reads "Futures", not "Spot")*: for every strike present in **both** `current.option_chain` and `previous.option_chain` (strikes only in one or the other are skipped this cycle — the "strikes entering/leaving the window" edge case — not fabricated), classify call-side and put-side independently using the standard futures-style joint read, applied to that side's own OI against **futures price direction** (`current.futures_ltp` vs `previous.futures_ltp`), not the option's own premium:

| Futures | OI | Classification |
|---|---|---|
| ↑ | ↑ | Long buildup |
| ↑ | ↓ | Short covering |
| ↓ | ↑ | Short buildup |
| ↓ | ↓ | Long unwinding |

**Why futures, not per-option premium:** `OptionStrike` (TASK-002) carries `call_oi`/`put_oi`/`call_iv`/`put_iv`/greeks — **no `call_ltp`/`put_ltp` premium field**. True per-strike buildup classification (option's own premium vs its own OI) needs that field, which doesn't exist in ingestion today, and the human declined to add it (see Blocking Dependencies) — futures price direction is the standard NSE F&O "buildup" convention anyway, not a fallback proxy, so this is the intended v1 design rather than a workaround.

Aggregate: bucket `{long_buildup, short_buildup}` → "buildup" (fresh conviction, OI growing → GO-favorable), `{short_covering, long_unwinding}` → "unwind" (positions closing, OI shrinking → NO-GO-favorable). `raw_value` = OI-weighted fraction of (strike, side) pairs classified into the "buildup" bucket this cycle, already naturally on `0.0–1.0` with `0.5` = balanced, so `sub_score = raw_value` directly (no extra rescaling needed — flagged as an explicit exception to the usual raw≠score separation, since here they're the same number by construction). `previous is None` → `(None, band, 0.5, reason)`.

**3. `oi_wall_proximity_and_strength`:** wall = strike with max `call_oi` (resistance side) and strike with max `put_oi` (support side) across `current.option_chain`, independent of position relative to spot (standard practitioner convention, not "nearest big strike above/below"). `proximity_pct = min(|call_wall_strike - spot|, |spot - put_wall_strike|) / spot * 100` — closest wall on either side, unsigned. Strength trend for that same nearest wall's strike (tracked **by strike**, not by "whichever strike was the wall last cycle" — if the wall itself migrated strikes between cycles, this compares the *current* wall strike's own OI now vs its own OI last cycle, which could itself be `None` if that strike wasn't previously the wall's neighbor... handled by treating "OI history for a strike not present in `previous.option_chain`" as missing, same as buildup's per-strike skip rule):
```
strength_trend = (wall_oi_now - wall_oi_prev) / wall_oi_prev   # None if wall strike absent from previous snapshot
proximity_score = _percentile_rank(proximity_pct, trailing_wall_proximity_history)      # far -> 1.0
strength_score  = _percentile_rank(-strength_trend, trailing_wall_strength_trend_history) if strength_trend is not None else 0.5  # decaying wall -> 1.0
sub_score = 0.5 * proximity_score + 0.5 * strength_score
```
Mirrors TASK-004's `spot_distance_from_flip` design language on purpose: a wall is a pin/magnet, same as the zero-gamma flip — close + strengthening = pin risk = NO-GO-favorable; far or decaying = GO-favorable. `raw_value = proximity_pct` (the headline number); strength trend surfaced via `template_reason`'s `context` param, same treatment as PCR's level.

**4. `max_pain_drift`:** max pain computed by the standard method — for each candidate strike `S` in the traded range, total writer payout `= Σ call_oi_k · max(0, S − k) + Σ put_oi_k · max(0, k − S)`, `max_pain = argmin_S(payout)`. No interpolation (unlike TASK-004's flip level) — max pain is reported as an actual listed strike, always exactly minimizing the summed payout. `raw_value = current_max_pain − session_open_max_pain` (points), where `session_open_max_pain: float | None` is a caller-supplied session-anchored reference (same pattern as TASK-003's `session_reference_price` — a passed-in value, never computed from wall-clock time inside this module). `sub_score = 0.5 + 0.5 * _percentile_rank(abs(drift), trailing_max_pain_drift_history)` — same direction-agnostic-movement-is-informative shape as PCR ROC. `session_open_max_pain is None` (first cycle) → `(None, band, 0.5, reason)`.

**Edge case — OI update lag from exchange:** not detectable or correctable at this layer (no per-strike freshness timestamp exists in `OptionStrike`, only a whole-snapshot `ts`). Noted as an inherited data-quality limitation of the current ingestion contract, not something `oi_structure.py` can special-case — flagging so it's a known, accepted gap rather than a silently swallowed one.

**Alternative considered:** per-strike premium-based buildup classification, adding `call_ltp`/`put_ltp` to `OptionStrike` now. Rejected for this ADR specifically to avoid stacking a second ingestion amendment mid-flight while TASK-003's VIX amendment is still pending human sign-off — bundled into the same "Blocking Dependencies" ask instead so it's one decision, not two sequential ones.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/signals/oi_structure.py` | Four sub-signal functions + private helpers (`_pcr`, `_classify_buildup`, `_walls`, `_max_pain`) |
| `src/aeolus/explain/reason.py` | *(amended, see TASK-003 ADR)* `template_reason` gains optional `context: dict[str, float] | None` |

## API Contracts

```python
# src/aeolus/signals/oi_structure.py
def pcr_level_and_roc(
    current: IngestionSnapshot,
    previous: IngestionSnapshot | None,
    trailing_pcr_roc_magnitude_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """raw_value = pcr_now - pcr_prev. previous is None -> (None, band, 0.5, reason)."""

def oi_buildup_classification(
    current: IngestionSnapshot,
    previous: IngestionSnapshot | None,
    reference_band: tuple[float, float],
) -> SignalResult:
    """OI-weighted fraction of (strike, side) pairs in {long_buildup, short_buildup}
    among strikes present in both snapshots. Classified from OI direction vs
    futures price direction (see Decision — no per-option premium available).
    previous is None -> (None, band, 0.5, reason)."""

def oi_wall_proximity_and_strength(
    current: IngestionSnapshot,
    previous: IngestionSnapshot | None,
    trailing_wall_proximity_history: list[float],
    trailing_wall_strength_trend_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """raw_value = percent distance to nearest of {max-call-OI strike, max-put-OI
    strike}. sub_score blends proximity + strength-trend percentiles 50/50."""

def max_pain_drift(
    current: IngestionSnapshot,
    session_open_max_pain: float | None,
    trailing_max_pain_drift_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """raw_value = current_max_pain - session_open_max_pain.
    session_open_max_pain is None -> (None, band, 0.5, reason)."""
```

## Blocking Dependencies — RESOLVED 2026-07-03

**Human decision:** decline the `call_ltp`/`put_ltp` ingestion amendment entirely. `oi_buildup_classification` uses **`futures_ltp`** (already in `IngestionSnapshot` since TASK-002 — no ingestion change needed) as the price-direction signal instead of `spot_ltp`, applied against each strike's own OI. This is the standard NSE F&O "buildup" convention — futures price + OI direction, not spot — and a better-motivated proxy than spot for this specific classification than the original Decision section proposed. Shipped as the permanent v1 design, not an interim one; no further trigger to revisit unless backtesting shows it's materially wrong.

**What changed vs. the original Decision section above:** every occurrence of `current.spot_ltp` / `previous.spot_ltp` in `oi_buildup_classification`'s price-direction read is `current.futures_ltp` / `previous.futures_ltp` instead. The classification table (Spot/OI → Long buildup/Short covering/Short buildup/Long unwinding) is unchanged in shape, just reads "Futures" instead of "Spot" as the price-direction column. No other function in this module is affected — `pcr_level_and_roc`, `oi_wall_proximity_and_strength`, and `max_pain_drift` never referenced spot or futures price direction in the first place.

## Performance / Failure Modes

- **No caching or internal state** — same discipline as TASK-003/004; every "previous" value is caller-supplied, every cycle.
- **Strike-set mismatch between cycles:** buildup classification and wall-strength trend both silently skip strikes absent from one side of the (current, previous) pair rather than erroring or fabricating a value for them.
- **Max-pain computation cost:** O(strikes²) worst case (payout summed per candidate strike across all strikes) — fine at typical NIFTY chain sizes (~40-80 strikes/expiry), flagging only so it isn't blindly reused on a much wider multi-expiry chain later without revisiting.

## Definition of Done

- [x] Integration-style tests calling all four public functions directly with realistic `IngestionSnapshot` pairs — no mocking of internals
- [x] `previous=None` / `session_open_max_pain=None` first-cycle test for every function that needs prior state — confirms the explicit `None`/`0.5` path, not a crash or a fabricated reading
- [x] Buildup classification test: construct a (current, previous) pair covering all four classification cells (long buildup / short covering / short buildup / long unwinding), assert correct bucket assignment — all four cells verified directly against `_classify_buildup`, plus one full public-function scenario
- [x] Strike-set-mismatch test: a strike present only in `current` (new listing) or only in `previous` (delisted/rolled) is excluded from that cycle's classification, not fabricated
- [x] Max-pain test against a hand-computed small chain (known correct answer) to catch an off-by-one in the payout-minimization loop
- [x] Constraint check: no per-signal veto (n/a — sub-scores only), no clock-time branching ("previous" is cycle-relative not wall-clock-relative, `session_open_max_pain` is a passed-in value), deterministic reasons (via TASK-010 stub + `context` amendment), polarity correct (movement/change = GO-favorable across all four, consistent with TASK-003/004's established direction)

**Implemented:** 2026-07-03. See `reports/debug/TASK-005_debug-report.md`, `reports/qa/TASK-005_qa-report.md`.
