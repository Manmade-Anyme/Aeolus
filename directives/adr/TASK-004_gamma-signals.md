# Architecture Decision Record — TASK-004

**Directive:** `directives/TASK-004_gamma-signals.md`
**Status:** APPROVED (2026-07-03)
**Date:** 2026-07-03

## Problem

Compute Spec §6.2's two gamma sub-signals — GEX/zero-gamma flip regime (sign **and** magnitude) and spot's distance from the flip level — as pure functions on the same `SignalResult` contract established in [TASK-003 ADR](TASK-003_volatility-signals.md). Both consume only `option_chain` (per-strike OI + greeks) and `spot_ltp` from `IngestionSnapshot` — no new ingestion gaps this time (unlike TASK-003's VIX gap).

## Decision

Two pure functions in `src/aeolus/signals/gamma.py`, reusing `SignalResult` and the shared `_percentile_rank` helper from `signals/contract.py` (TASK-003 ADR amended to promote that helper out of `volatility.py` so both modules share it instead of duplicating it).

**GEX sign convention (stated explicitly — this is the assumption most likely to be wrong if copied from a reference tool, per constraint #4):** dealers are assumed net **long calls / short puts is NOT used** — this module uses the standard "dealer is long gamma on calls sold to them, short gamma on puts sold to them" convention seen in SpotGamma-style tools:
```
net_gamma_at_strike = call_gamma * call_oi - put_gamma * put_oi
```
Positive `net_gamma_at_strike` summed across the chain → dealers net **long** gamma → dealers hedge by trading *against* price moves → **dampening/pinning** regime. Negative → dealers net **short** gamma → hedge *with* price moves → **amplifying/trending** regime. This sign convention is a genuine assumption (Dhan doesn't tell us dealer positioning directly, greeks are per-strike market values only) — flagging for sign-off same as TASK-003's IV-RV spread call, since a flipped sign here silently inverts every gamma-based reason string without any type error to catch it.

**Function 1 — `gex_regime`:** sums `net_gamma_at_strike` (dollar-scaled: `× spot_ltp² × lot_size × 0.01`, standard GEX dollar convention) across the whole chain → `raw_value`. Sign gives regime direction; magnitude is normalized against `trailing_gex_magnitude_history` (caller-supplied `list[float]` of past `|net_gex|` readings, same caller-supplied-history convention as TASK-003) via the shared `_percentile_rank` helper. Combined into one `0.0–1.0` score:
```
magnitude_pct = _percentile_rank(abs(net_gex), trailing_gex_magnitude_history)  # 0..1
sub_score = 0.5 + 0.5 * magnitude_pct   if net_gex < 0   (negative/amplifying → GO-favorable)
sub_score = 0.5 - 0.5 * magnitude_pct   if net_gex >= 0  (positive/dampening → NO-GO-favorable)
```
This satisfies the directive's explicit "weak negative gamma scores differently from strong negative gamma" requirement — a weak negative reading sits near `0.5` (barely favorable), a strong one approaches `1.0`.

**`lot_size`:** passed in as a parameter (config-sourced constant), not read from `IngestionSnapshot` — NIFTY lot size is static exchange contract metadata (revised occasionally by NSE circular, not a live market value), so it doesn't belong in the per-cycle ingestion snapshot. No new ingestion gap.

**Function 2 — `spot_distance_from_flip`:** the "zero-gamma flip level" is approximated as the **strike** where the cumulative sum of `net_gamma_at_strike` (strikes sorted ascending, chain-order — not dollar-scaled, since the constant `spot² × lot_size × 0.01` multiplier doesn't move where the sum crosses zero) changes sign, linearly interpolated between the two bracketing strikes. This is a practical proxy, not a true Black-Scholes-recentered flip (that would require recomputing every strike's gamma at hypothetical spot levels, which needs a full IV surface, not just per-strike greeks at current spot — out of scope, ingestion doesn't give us that). Documenting this explicitly since it's the standard simplification most public GEX dashboards make, but it's still an approximation worth the human knowing about.

`raw_value` = signed `(spot_ltp - flip_level) / spot_ltp × 100` (percent distance, not absolute points — keeps `reference_band` thresholds stable as NIFTY's index level drifts over months, instead of going stale like a fixed points-based band would). `sub_score` is driven by **magnitude of distance only**, sign-independent: further from the flip level (in either direction) → higher score. Rationale: the flip level itself acts as a pin/magnet in GEX practitioner literature — price sitting near it is the ambiguous, mean-reversion-prone zone (NO-GO-favorable, "quiet/pinned"), while being well clear of it in either direction means today's regime (whichever `gex_regime` says it is) is more likely to hold, not flip intraday (GO-favorable). This keeps function 2 additive rather than duplicating function 1's sign — it answers "how stable is the regime," not "which regime."

**Edge case — flip level outside traded strike range:** if `net_gamma_at_strike` never changes sign across the whole chain (monotonic), no interpolated flip level exists within observable data. `spot_distance_from_flip` returns `(None, reference_band, 0.5, reason)` explicitly stating this — it does **not** extrapolate a flip level beyond the last traded strike. `gex_regime` is unaffected (it only needs total net GEX, not the crossing point) and still returns a real reading.

**Edge case — thin OI:** if total chain OI (`Σ call_oi + put_oi`) falls below a caller-supplied `min_total_oi` threshold, both functions return the insufficient-data path (`None`/`0.5`/explicit reason) rather than computing a GEX reading off a sparse, noisy book.

**Edge case — early-session instability:** explicitly **not** handled inside `gamma.py` — deciding "is it early in the session, discount this reading" is a time-based interpretation call, which constraint #2 reserves for the scheduler/composite layer (e.g. TASK-008 could down-weight the gamma category's contribution during an early window by *config*, not by `gamma.py` branching on the clock). Noting this so the edge case isn't silently dropped, just correctly relocated.

**Alternative considered:** true BS-recentered zero-gamma flip (recompute every strike's gamma at a grid of hypothetical spot levels using IV per strike, sum, find crossing). Rejected for v1 — needs an options-pricing dependency and a chosen day-count/rate convention this repo doesn't have yet, for a refinement over the strike-domain proxy that's unlikely to change which regime side of the flip spot sits on for signal purposes. Worth revisiting if backtesting later shows the proxy is materially wrong.

**Alternative considered:** signed distance (not magnitude-only) feeding `sub_score` directly, treating "above flip" as inherently more bullish and "below" as bearish. Rejected — AEOLUS has no directional (up/down) polarity anywhere in the spec, only GO/NO-GO (movement vs quiet); a signed distance would smuggle in directional bias this system isn't designed to express.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/signals/gamma.py` | Two sub-signal functions + private helpers (`_net_gamma_by_strike`, `_flip_level`) |
| `src/aeolus/signals/contract.py` | *(amended from TASK-003)* `_percentile_rank` promoted here, now shared by `volatility.py` and `gamma.py` |

## API Contracts

```python
# src/aeolus/signals/gamma.py
def gex_regime(
    option_chain: list[OptionStrike],
    spot_ltp: float | None,
    lot_size: int,
    trailing_gex_magnitude_history: list[float],
    reference_band: tuple[float, float],
    min_total_oi: int = 0,
) -> SignalResult:
    """Signed, dollar-scaled net GEX across the chain. Sign = regime
    (negative -> amplifying/GO-favorable, positive -> dampening/NO-GO-favorable).
    Magnitude normalized via shared _percentile_rank against trailing history.
    Thin-OI / missing spot -> (None, reference_band, 0.5, reason)."""

def spot_distance_from_flip(
    option_chain: list[OptionStrike],
    spot_ltp: float | None,
    reference_band: tuple[float, float],
    min_total_oi: int = 0,
) -> SignalResult:
    """Percent distance of spot_ltp from the interpolated zero-gamma flip
    strike (strike-domain proxy, not BS-recentered). sub_score driven by
    |distance| only (further from flip -> higher score). No sign-changing
    crossing found in chain, or thin OI -> (None, reference_band, 0.5, reason)."""
```

## Performance / Failure Modes

- **No caching or state** — pure functions of their arguments, same as `volatility.py`.
- **Interpolation, not extrapolation** — the flip level is only ever reported when it falls strictly within the traded strike range; never projected beyond the last strike.
- **`lot_size` correctness is the caller's responsibility** — this module doesn't validate it against Dhan's instrument master; a stale/wrong `lot_size` silently scales `gex_regime`'s dollar magnitude without erroring. Worth a comment at the call site (TASK-008/TASK-013) pointing at where the authoritative value lives, but that's those modules' ADRs.

## Definition of Done

- [x] Integration-style tests calling both public functions directly with realistic `option_chain` inputs — no mocking of internals
- [x] Sign convention test: construct a chain with dominant call gamma/OI vs dominant put gamma/OI, assert `gex_regime`'s sign and `sub_score` direction match the documented convention
- [x] Magnitude test: same sign, two different `trailing_gex_magnitude_history` percentile positions -> different `sub_score`, weak case closer to `0.5` than strong case
- [x] Flip-level-outside-range test: monotonic-sign chain -> `spot_distance_from_flip` returns `None` raw_value, not an extrapolated guess
- [x] Thin-OI test: below `min_total_oi` -> both functions return the insufficient-data path
- [x] Constraint check: no per-signal veto (n/a — sub-scores only), no clock-time branching (early-session handling explicitly deferred to config/scheduler, not implemented here), deterministic reasons (via TASK-010 stub), polarity correct (sign convention table above, magnitude-only distance scoring)

**Implemented:** 2026-07-03. See `reports/debug/TASK-004_debug-report.md`, `reports/qa/TASK-004_qa-report.md`. `_clamp01` promoted to `signals/contract.py` (shared with `volatility.py`'s `expected_move_consumed_ratio`).
