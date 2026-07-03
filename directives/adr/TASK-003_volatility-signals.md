# Architecture Decision Record — TASK-003

**Directive:** `directives/TASK-003_volatility-signals.md`
**Status:** APPROVED (2026-07-03)
**Date:** 2026-07-03

## Problem

Compute Spec §6.1's four volatility sub-signals — IV percentile/rank (20–60d trailing), IV vs RV spread, India VIX level + rate of change, ATM straddle expected-move-consumed ratio — each as a pure function returning the standard `(raw_value, reference_band, sub_score, reason_string)` tuple. This is the **first** category module (TASK-004..007 follow the same shape), so this ADR also fixes two conventions every later category module inherits: the `SignalResult` type shape and the `sub_score` scale.

**Two gaps surfaced while writing this ADR, flagged for explicit approval alongside the design below** (see "Blocking Dependencies"):
1. `IngestionSnapshot` (TASK-002, already merged to `main`) has no India VIX field — VIX is not ingested today.
2. Trailing IV/RV/VIX history has no defined source. `signal_snapshots` is write-only from TASK-008's side (per `docs/DATA_MODEL.md`: "Signal modules... never write here directly"), so a volatility-module function cannot reach into Supabase itself for its own history.

## Decision

Four pure functions in `src/aeolus/signals/volatility.py`, one per sub-signal, no I/O, no Supabase client, no Dhan client — matching the existing convention (`docs/DATA_MODEL.md`: signal modules only ever return the 4-tuple to TASK-008, they don't persist or fetch). All historical series (trailing IV, trailing spot prices for RV, VIX history for ROC) are passed in as arguments by the caller. **Who the caller is, and where it sources those series, is out of scope for this ADR** — most likely TASK-013 (scheduler) or TASK-008 (composite engine) querying `signal_snapshots.raw_readings` for prior cycles, but that wiring is that module's ADR to make, not this one's, per the same boundary TASK-002 drew around itself.

**`SignalResult` convention (established here, binding on TASK-004..007):**
```python
SignalResult = tuple[float | None, tuple[float, float], float, str]
# (raw_value, reference_band=(low, high), sub_score, reason_string)
```
- `raw_value: float | None` — `None` only when the underlying input is genuinely missing (VIX unavailable, IV missing for the ATM strike, insufficient trailing history). Never fabricated or interpolated, consistent with the ingestion layer's "every `None` is a real don't-know" rule.
- `reference_band: tuple[float, float]` — the numeric `(low, high)` band `raw_value` is being compared against. Kept numeric (not a pre-formatted string) so the reason-string templater (TASK-010) builds the phrase from real numbers, per constraint #3 — never a string baked in by this module.
- `sub_score: float` — **normalized `0.0`–`1.0`, where `1.0` = maximally favorable for GO (directional buying), `0.0` = maximally favorable for NO-GO (quiet/pinned).** `0.5` is neutral — used both as a genuine midpoint reading and as the explicit "insufficient/missing data" fallback (no opinion, not a fabricated lean either way). This scale is what TASK-008 will weight and sum; every later category module must emit on the same `0.0`–`1.0` scale for the weighted sum to mean anything.
- `reason_string: str` — produced by an interim stub (see below), signature-compatible with TASK-010 so nothing in `volatility.py` changes when TASK-010 lands for real.

**Polarity per sub-signal (constraint #4 — this is the part most likely to get silently inverted, so spelling out the reasoning per signal):**

| Sub-signal | High raw value means | sub_score direction | Why (buying tool, not selling tool) |
|---|---|---|---|
| IV percentile/rank | IV rich vs its own 20–60d range | **higher % → higher score** | Low IV percentile = complacent/quiet regime = NO-GO. This is the *inverse* of a premium-selling tool (where high IV rank = GO-to-sell). Explicitly the constraint #4 case. |
| IV vs RV spread | *(redesigned — see below)* | **IV's own trend, not spread level, is now the primary driver** | Original design scored the static spread level. Overridden by human field feedback (2026-07-03) before implementation — see "IV trend redesign" below. |
| VIX level + ROC | VIX elevated / rising | **higher level & positive ROC → higher score** | Same direction as IV percentile, same reasoning. |
| Expected-move-consumed ratio | Realized move ≥ what the straddle priced in | **higher ratio → higher score** | Directly stated in spec §6.1 as "is the day delivering on what premium priced in" — a low ratio late in session is the clearest single quiet/pinned tell. |

**IV trend redesign (human field feedback, 2026-07-03 — overrides the original design before any code was written):** original design scored `iv_rv_spread`'s **level** (IV minus realized vol) — higher spread scored lower (NO-GO). Human trading feedback directly contradicted the premise: *"when IV is negative [falling] or reducing, I end up not getting proper moves. Even if IV is high but it's reducing I get in trouble. Expensive premium is not the issue."* This is a known options-buying mechanic (falling IV = vega headwind on a long-premium position, and falling IV often precedes/accompanies a quiet consolidation) that the level-only spread design didn't capture at all — a session could sit at a rich-looking IV-vs-RV spread while IV is actively bleeding out, and the original formula would've scored it NO-GO-mild-favorable-ish, exactly backwards from lived experience.

**Redesigned:** `iv_rv_spread`'s `raw_value` and scoring are now driven primarily by **IV's own trend** (`current_iv - previous_iv`, cycle-to-cycle), not the static spread level:
```
iv_trend = current_iv - previous_iv
sub_score = 0.5 + 0.5 * _percentile_rank(iv_trend, trailing_iv_rising_magnitude_history)    if iv_trend > 0   (rising IV -> GO-favorable)
sub_score = 0.5 - 0.5 * _percentile_rank(abs(iv_trend), trailing_iv_falling_magnitude_history) if iv_trend < 0 (falling IV -> NO-GO-favorable, dominant regardless of absolute IV level — directly encodes "even if IV is high but falling, still bad")
sub_score = 0.5                                                                                if iv_trend == 0
```
`raw_value = iv_trend` (signed) — the number that actually drives the score, per constraint #3. The original spec ask ("IV vs RV spread," §6.1) is still satisfied but demoted to context: `spread = current_iv - realized_vol` is still computed and surfaced via `template_reason`'s `context` param, informative but no longer what `sub_score` is built from. Needs `previous_iv` — same `current`/`previous` cycle-relative shape TASK-005 introduced, extended here into what was originally a pure-history-list function; `iv_rv_spread` now takes both `previous_iv` (cycle-relative) and `trailing_spot_history` (for the still-computed RV context).

**Reason-string interim stub:** `src/aeolus/explain/reason.py`, one function `template_reason(signal_name: str, raw_value: float | None, reference_band: tuple[float, float], sub_score: float) -> str`, matching TASK-010's stated call signature (`(raw_value, reference_band, sub_score)` + signal identity) so `volatility.py`'s call sites don't change when TASK-010 supersedes the stub. Deterministic string formatting, pinned float rounding (2 decimals), explicit `"{signal_name}: no data"`-shaped output on `raw_value is None` — matching TASK-010's directive edge cases even though this is only the stub.

**RV estimator — close-to-close, not Parkinson/Garman-Klass:** ingestion (TASK-002) only ever exposes `spot_ltp` ticks, never session OHLC bars, so range-based estimators aren't feasible from data this system actually has. `realized_vol` is computed inside `volatility.py` from a trailing daily-close price series (annualized stdev of log returns), same lookback window as the IV history. This is a data-availability constraint, not a statistical-purity choice — flagging so it's a visible decision rather than a silent default.

**Lookback handling:** `MIN_LOOKBACK_SESSIONS = 20`, caller may supply up to 60. Below 20 sessions of history, `iv_percentile_rank` (and `iv_rv_spread`, which shares the same series) returns `(None, band, 0.5, reason)` — the directive's "insufficient trailing history early after go-live" edge case — rather than computing a percentile off a too-small sample.

**Reference bands are caller-supplied, not hardcoded:** spec §8 requires IV-percentile bands to recalibrate lower on expiry day. Hardcoding bands inside `volatility.py` would bake non-expiry assumptions into every call. Every function takes its `reference_band` boundaries as a parameter (sourced from `config/` expiry vs non-expiry tables, TASK-008's territory) rather than a module-level constant — keeps `volatility.py` config-agnostic and reusable across both configs untouched.

**Alternative considered:** compute RV/IV history internally via a Supabase read inside `volatility.py`. Rejected — breaks the established boundary (`docs/DATA_MODEL.md`) that only TASK-008 touches `signal_snapshots`, and would silently duplicate query logic once TASK-004..007 each need their own trailing series.

**Alternative considered:** `sub_score` on a `-1.0..1.0` scale (negative = NO-GO lean, positive = GO lean). Rejected in favor of `0.0..1.0` — a weighted sum of signed scores can cancel to exactly `0.0` for two different reasons (all-neutral vs. balanced-opposite-conviction), which is a worse property for an explainable composite than a `0.0..1.0` scale where `0.5` unambiguously means neutral/unknown.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/signals/volatility.py` | Four sub-signal functions + private helpers (`_atm_strike`, `_realized_vol`); imports shared `_percentile_rank` from `contract.py` |
| `src/aeolus/signals/contract.py` | `SignalResult` type alias + shared `_percentile_rank` helper — shared by TASK-004..007, defined here as the first category module |
| `src/aeolus/explain/reason.py` | Interim `template_reason()` stub, signature-compatible with TASK-010 |

## API Contracts

```python
# src/aeolus/signals/contract.py
SignalResult = tuple[float | None, tuple[float, float], float, str]

# src/aeolus/signals/volatility.py
def iv_percentile_rank(
    current_iv: float | None,
    trailing_iv_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """20-60d percentile rank of current_iv within trailing_iv_history.
    None / <20 sessions of history -> (None, reference_band, 0.5, reason)."""

def iv_rv_spread(
    current_iv: float | None,
    previous_iv: float | None,
    trailing_spot_history: list[float],
    trailing_iv_rising_magnitude_history: list[float],
    trailing_iv_falling_magnitude_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """raw_value = current_iv - previous_iv (IV's own trend, dominant driver
    per human field feedback — falling IV is NO-GO-favorable regardless of
    absolute level). RV spread (current_iv - realized_vol, computed from
    trailing_spot_history) still surfaced via template_reason's context param,
    no longer drives sub_score. previous_iv is None (first cycle) ->
    (None, reference_band, 0.5, reason)."""

def vix_level_and_roc(
    current_vix: float | None,
    trailing_vix_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """VIX level + rate of change over trailing_vix_history.
    current_vix is None whenever IngestionSnapshot has no VIX field/value
    (see Blocking Dependencies) -> (None, reference_band, 0.5, reason)."""

def expected_move_consumed_ratio(
    spot_ltp: float | None,
    session_reference_price: float | None,
    straddle_implied_expected_move: float | None,
    reference_band: tuple[float, float],
) -> SignalResult:
    """abs(spot_ltp - session_reference_price) / straddle_implied_expected_move.
    Live-only per directive; any None input -> (None, reference_band, 0.5, reason)."""

# src/aeolus/explain/reason.py
def template_reason(
    signal_name: str,
    raw_value: float | None,
    reference_band: tuple[float, float],
    sub_score: float,
) -> str:
    """Deterministic, TASK-010-signature-compatible. raw_value=None -> explicit
    '{signal_name}: no data' string, never fabricated."""
```

**Amendment (from TASK-005 ADR):** `template_reason` gains an optional `context: dict[str, float] | None` keyword parameter — extra non-scoring numbers (e.g. PCR's current level, alongside the ROC that actually drives `sub_score`) surfaced in the string for human readability without changing what drives the score. Superset of TASK-010's stated `(raw_value, reference_band, sub_score)` + identity signature — flagged for confirmation that TASK-010, when built, keeps this optional param rather than dropping it.

## Blocking Dependencies

**#1 and #2 below: RESOLVED, merged 2026-07-03 (PR #4, commit `b9dbaac`)** — `india_vix` and `lot_size` both live in `IngestionSnapshot`/`IngestionService` now. Left in place for the historical record of why they're there.

1. **India VIX is not currently ingested — now confirmed available, low risk.** `IngestionSnapshot` (TASK-002, merged) has no VIX field. Verified directly against Dhan's live `api-scrip-master-detailed.csv` (2026-07-03): `India VIX` is listed at `SECURITY_ID=21`, `EXCH_ID=NSE`, `SEGMENT=I` — the exact same index segment (`I`, i.e. `IDX_I`) NIFTY spot already subscribes through. Not a new segment, not a new vendor, not a guess — a real security_id confirmed present today. Proposal: a small addendum to TASK-002 — add `india_vix: float | None` to `IngestionSnapshot`, subscribed alongside spot LTP on the existing WS `IDX_I` path. Needs a companion PR (`TASK-002b` or folded into TASK-003's PR, human's call) before `vix_level_and_roc` can run against real data. Until then it runs `current_vix=None` end to end.
   - **Bonus finding, unrelated to VIX but surfaced by the same query:** the same scrip master also lists `Gift Nifty` at `SECURITY_ID=5024`, `EXCH_ID=NSE`, `SEGMENT=I` — which appears to contradict TASK-002 ADR's merged, checked-off conclusion that GIFT Nifty is "structurally absent" from Dhan (that conclusion was reached by reading the `dhanhq` SDK's Python-level segment *constants*, not by querying live instrument data). Flagging this as a separate, higher-priority item for the human to look at — it's not part of this ADR's scope, but it means a shipped Definition-of-Done item on a merged module may be wrong. Recommend checking whether `SECURITY_ID=5024` actually returns live-updating ticks before concluding anything — could be a stale/placeholder listing.
2. **`lot_size` (needed by TASK-004's `gex_regime`) is already in the same scrip master** (`LOT_SIZE` column) that TASK-002's `instruments.py` already parses — just not currently surfaced. Cheap addition to the same companion PR: expose `lot_size` for the NIFTY futures/options instrument alongside the existing `security_id` resolution, rather than TASK-004 needing its own separate constant.
3. **Trailing-history sourcing ownership.** This ADR fixes the *shape* (`list[float]`, caller-supplied) but not who fetches it. Confirm TASK-013 (scheduler) or TASK-008 (composite engine) — whichever module's ADR is written next — takes ownership of querying `signal_snapshots.raw_readings` for the trailing series and shaping it into these functions' arguments.

## Performance / Failure Modes

- **Missing ATM strike / missing IV for ATM strike:** `_atm_strike` picks the closest `strike` to `spot_ltp` from `option_chain`; if that strike's `call_iv`/`put_iv` is itself unusable (e.g. zero/negative from a bad tick), `current_iv` resolves to `None` for that cycle rather than falling back to a nearby strike — no silent substitution.
- **`option_chain` empty:** all four functions degrade to their `None`/`0.5`/explicit-reason path; no exception raised, consistent with `IngestionService.latest()` never raising on data gaps.
- **No internal caching or state** — every call is a pure function of its arguments; nothing in `volatility.py` remembers the previous cycle. `iv_rv_spread`'s `previous_iv` is caller-supplied per cycle, same discipline as TASK-005's `current`/`previous` pattern — first cycle of the day (`previous_iv is None`) degrades to `(None, band, 0.5, reason)`.

## Definition of Done

- [x] Integration-style tests calling each of the four public functions directly with realistic `IngestionSnapshot`-shaped inputs — no mocking of internals
- [x] Polarity test per sub-signal: assert `sub_score` moves in the documented direction as `raw_value` moves (catches an accidental sign flip on any of the four, especially IV-vs-RV spread)
- [x] Insufficient-history test: <20 sessions -> `(None, band, 0.5, reason)`, not a computed percentile
- [x] `current_vix=None` end-to-end test (VIX not yet wired) confirms `vix_level_and_roc` degrades cleanly, doesn't crash the module
- [x] `template_reason()` determinism test: same inputs -> byte-identical string, including the `None`-raw-value path
- [x] Constraint check: no per-signal veto (n/a — these are sub-scores only, no state decision here), no clock-time branching (`session_reference_price` is a passed-in value, never computed from wall-clock time inside this module), deterministic reasons (yes, via stub), polarity correct (see table above, esp. IV-vs-RV spread)

**Implemented:** 2026-07-03. See `reports/debug/TASK-003_debug-report.md`, `reports/qa/TASK-003_qa-report.md`.
