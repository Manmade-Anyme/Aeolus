# Architecture Decision Record — TASK-008

**Directive:** `directives/TASK-008_composite-state-machine.md`
**Status:** APPROVED (2026-07-03)
**Date:** 2026-07-03

## Problem

Combine TASK-003..007's ~16 sub-signal outputs into one composite score, map it to `NO_GO`/`PREPARE`/`GO` via config-driven thresholds, apply mandatory hysteresis before any flip, write `signal_snapshots` every cycle and `state_transitions` only on confirmed flips.

**This is the first module in the project that does real I/O.** Every prior signal module (TASK-003..007) is a pure function suite — no Supabase client, no Dhan client, all history caller-supplied. Each of their ADRs independently deferred a caller-side state question to "TASK-008/013" without saying which:

| Deferred by | What | Shape |
|---|---|---|
| TASK-003 | `trailing_iv_history`, `trailing_spot_history`, `previous_iv`, `trailing_vix_history`, `trailing_iv_rising/falling_magnitude_history` | cross-session lists (20-60 sessions) + one cycle-relative scalar |
| TASK-004 | `trailing_gex_magnitude_history`, `lot_size`, `min_total_oi` | cross-session list + static constant + config knob |
| TASK-005 | `previous: IngestionSnapshot`, `trailing_pcr_roc_magnitude_history`, `trailing_wall_proximity/strength_trend_history`, `session_open_max_pain`, `trailing_max_pain_drift_history` | cycle-relative snapshot + cross-session lists + session-scoped scalar |
| TASK-006 | `cvd_delta_history`, `price_history`, `established_range`, `average_daily_volume`, `trailing_cvd/imbalance_magnitude_history`, `trailing_excursion_history` | session-scoped growing lists + session-scoped scalar (persisted via `template_reason`'s `context` param, ADR explicitly asks TASK-008/013 to "formalize a proper mechanism") |
| TASK-007 | `cycle_price_volume_history`, `session_open`, `basis_history`, `prior_day_high/low/close/value_area`, `trailing_average_range/gap/basis_magnitude_history` | session-scoped growing lists + cross-session prior-day scalars (seeded from the previous session_date's final `signal_snapshots` row, per that ADR's Blocking Dependency #3) |

**Human-confirmed 2026-07-03, both foundational to everything below:**
1. **Aggregation:** category score = **equal-weighted average** of that category's sub-signal scores. Only the 5 category weights are config knobs — not ~16 per-sub-signal weights. Matches the spec's literal wording ("weighted sum of the five category sub-scores") and the same reasoning Open Decision #2 already used to reject graduated DTE weighting: no `outcome_labels` data exists yet to calibrate finer-grained weights, so don't add knobs finer than the spec asks for.
2. **State ownership:** `engine.py` (this module) owns a Supabase client directly and is responsible for seeding *and* persisting all of the above. TASK-013 (scheduler, not yet built) stays pure orchestration — "when to run" — and never touches `signal_snapshots` itself. This is a deliberate, first-of-its-kind exception to TASK-003..007's zero-I/O rule, not a precedent for those modules; `docs/DATA_MODEL.md` already named this module "the sole writer of `signal_snapshots`/`state_transitions`," being the sole *reader* of its own table for seeding is the same boundary, not a new one.

## Decision

### 1. Config: ARES's exact pattern, human-confirmed — pydantic-settings, no YAML

**Not** YAML files loaded at runtime (the earlier draft of this ADR proposed `config/expiry.yaml`/`non_expiry.yaml`) — human directed an exact match to how ARES does it: `pydantic-settings`, fail-fast, two **complete config instances defined as Python objects**, not external data files. Two files in `config/` (this repo's already-established location for these tables, per `CLAUDE.md`'s layout — ARES itself keeps its equivalent files inside its own package, the location differs, the pattern doesn't):

- `config/tuning.py` — the `EngineConfig` schema (a `pydantic_settings.BaseSettings` subclass, so any field can still be overridden by an env var for ops flexibility, exactly like ARES's `TuningConfig`), with **no default values on the fields that are judgment-calibrated knobs** — a missing field is a startup error, never a silent zero.
- `config/profiles.py` — two **complete, hardcoded** instances, `EXPIRY_CONFIG = EngineConfig(...)` and `NON_EXPIRY_CONFIG = EngineConfig(...)`, each populated with every field explicitly. No partial construction, no inheritance/merging between the two — same discipline Open Decision #2 already committed to, now literally matching ARES's `config_profiles.py` naming and shape.

```python
# config/tuning.py
from pydantic import model_validator
from pydantic_settings import BaseSettings

class CategoryWeights(BaseSettings):
    volatility: float
    gamma: float
    oi_structure: float
    order_flow: float
    context: float

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "CategoryWeights":
        total = self.volatility + self.gamma + self.oi_structure + self.order_flow + self.context
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"category weights must sum to 1.0, got {total}")
        return self

class StateThresholds(BaseSettings):
    no_go_prepare: float   # composite below this -> NO_GO
    prepare_go: float      # composite at/above this -> GO; between the two -> PREPARE

class EngineConfig(BaseSettings):
    category_weights: CategoryWeights
    reference_bands: dict[str, tuple[float, float]]   # keyed by sub-signal name, e.g. "iv_percentile_rank"
    thresholds: StateThresholds
    confirmation_cycles: int          # hysteresis window, see Decision §5
    min_total_oi: int                 # TASK-004/005 thin-book guard
    volume_participation_pct: float   # TASK-006
    min_range_width: float            # TASK-006
    order_flow_extreme_threshold: float  # TASK-006 delta_imbalance_and_absorption
    bucket_size: float                # TASK-007 histogram
    area_pct: float                   # TASK-007 value_area (0.70 human-confirmed)
    expansion_threshold: float        # TASK-007 profile shape
    context_extreme_threshold: float  # TASK-007 profile shape

# config/profiles.py
from .tuning import EngineConfig, CategoryWeights, StateThresholds

NON_EXPIRY_CONFIG = EngineConfig(
    category_weights=CategoryWeights(volatility=0.2, gamma=0.2, oi_structure=0.2, order_flow=0.2, context=0.2),
    thresholds=StateThresholds(no_go_prepare=0.4, prepare_go=0.6),
    ...  # every field, no defaults relied on
)
EXPIRY_CONFIG = EngineConfig(
    category_weights=CategoryWeights(volatility=0.15, gamma=0.3, oi_structure=0.3, order_flow=0.15, context=0.1),
    thresholds=StateThresholds(no_go_prepare=0.45, prepare_go=0.65),  # GO bar raised on expiry day, per Spec §8
    ...
)
```

`reference_bands` is one dict covering every sub-signal by name (`iv_percentile_rank`, `iv_rv_spread`, `vix_level_and_roc`, `expected_move_consumed_ratio`, `gex_regime`, `spot_distance_from_flip`, `pcr_level_and_roc`, `oi_buildup_classification`, `oi_wall_proximity_and_strength`, `max_pain_drift`, `cvd_direction_and_divergence`, `delta_imbalance_and_absorption`, `volume_participation_range`, `prior_day_profile_shape`, `gap_classification`, `futures_basis_drift`) — one dict rather than 16 named fields, so adding a 17th sub-signal later doesn't require a schema migration, just a new key. `lot_size` is deliberately **not** in `EngineConfig` — it's live instrument metadata from `IngestionService.lot_size` (TASK-002), passed to `gex_regime` at call time, not a judgment-calibrated knob.

**Fail-fast is structural, not a manual check:** `EngineConfig(BaseSettings)` raises `pydantic.ValidationError` the moment `EXPIRY_CONFIG`/`NON_EXPIRY_CONFIG` are constructed (module import time) if any field is missing or invalid — there is no separate "load and validate" step to forget to call, matching ARES's "if a required secret or configuration is missing, the system will fail to start" exactly. This also structurally satisfies the directive's "config file invalid at startup" edge case: there's no file to be invalid, a bad value is a Python-level construction error caught by every test that imports `config/profiles.py`.

**Config selection is binary, per Open Decision #2:** `config = EXPIRY_CONFIG if dte(session_date, expiry_date) == 0 else NON_EXPIRY_CONFIG`. `dte()` is TASK-007's function, called once per cycle by the engine (cheap, pure); the engine never touches a calendar itself, consistent with constraint #2.

**Values in `config/profiles.py` are judgment-calibrated placeholders, marked as such in a module-level comment** — per the directive's stated Output ("initial judgment-calibrated values"), not backtested. Revisit once `outcome_labels` (TASK-012) accumulates.

### 2. Aggregation: composite score

```python
category_score = sum(sub_scores_in_category) / len(sub_scores_in_category)
composite_score = (
    weights.volatility * category_score["volatility"]
    + weights.gamma * category_score["gamma"]
    + weights.oi_structure * category_score["oi_structure"]
    + weights.order_flow * category_score["order_flow"]
    + weights.context * category_score["context"]
)
```
`dte()` itself is **not** one of `context`'s three averaged sub-signals — per OPEN_DECISIONS #2 and the TASK-007 ADR, it's routing metadata (config selection), never a scored input. `context`'s category average is over `prior_day_profile_shape`, `gap_classification`, `futures_basis_drift` only.

**Partial-composite policy (directive's named edge case), resolved by construction, not special-cased:** every sub-signal function already degrades to `(None, band, 0.5, reason)` on missing/insufficient data — that's the existing, tested contract across all 16 functions. A category where every sub-signal is currently degraded therefore averages to exactly `0.5` (neutral) by the same mechanism already in place, and flows into the weighted sum like any other neutral reading. **No category is ever excluded or its weight redistributed** — doing so would mean a quiet feed gap could *change the effective weighting* of the categories still reporting, which is a subtler version of the per-signal-veto constraint 1 exists to prevent. If ingestion is degraded enough that most categories read `0.5`, the composite drifts toward the middle of the threshold band on its own; that's the correct, honest behavior for a system whose entire premise is "the sum decides," not a bug needing a workaround.

### 3. Safe-call wrapper: a bug is not a missing-data path

The existing `None`/`0.5` fallback in every sub-signal function handles *missing data* gracefully. It does not handle a genuine *exception* (a bug — e.g. a `KeyError` from a chain shape assumption that turns out wrong live). Per the directive's "category module returns error/missing" edge case, the engine wraps every one of the 16 calls:

```python
def _safe_call(name: str, fn: Callable[..., SignalResult], *args, **kwargs) -> SignalResult:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        band = (0.0, 0.0)  # placeholder band, real one substituted by caller before this returns
        return (None, band, 0.5, f"{name}: error ({exc.__class__.__name__})")
```
A crashing sub-signal degrades that one reading to neutral and is logged (real logging mechanism is an implementation detail, not an ADR concern) — it never takes down the whole cycle, and it never silently produces a fabricated score. This is a genuinely new decision this ADR introduces (no prior ADR needed it, since none of them called each other) — flagging it as such rather than implying the directive spelled out this exact mechanism.

### 4. `EngineState`: the seeding/persistence contract every prior ADR deferred here

One dataclass, held by `engine.py`, split into two lifetimes:

**Session-scoped (cleared explicitly at market close, not lazily detected at next day's first cycle — see Decision §7):**

| Field | Feeds |
|---|---|
| `previous_snapshot: IngestionSnapshot \| None` | TASK-005 (all 4 functions), TASK-003's `iv_rv_spread` (`previous_iv`) |
| `cvd_delta_history`, `price_history: list[float]` | TASK-006 `cvd_direction_and_divergence` |
| `established_range: tuple[float, float] \| None` | TASK-006 `volume_participation_range` |
| `cycle_price_volume_history: list[tuple[float, float]]` | TASK-007 histogram |
| `session_open: float \| None` | TASK-007 `gap_classification` (opening **futures** price) |
| `session_reference_price: float \| None` | TASK-003 `expected_move_consumed_ratio` (opening **spot** price — see §7a, deliberately separate from `session_open`) |
| `session_open_max_pain: float \| None` | TASK-005 `max_pain_drift` |
| `basis_history: list[float]` | TASK-007 `futures_basis_drift` (shares `price_history` above) |

**Cross-session (persist across day boundaries, seeded once at `engine.start()`):**

| Field | Feeds | Seeded from |
|---|---|---|
| `trailing_iv_history`, `trailing_spot_history`, `trailing_vix_history` | TASK-003 | last `MIN_LOOKBACK_SESSIONS`-to-60 prior `signal_snapshots` rows (one per session_date) |
| `trailing_iv_rising/falling_magnitude_history` | TASK-003 | same |
| `trailing_gex_magnitude_history` | TASK-004 | same |
| `trailing_pcr_roc_magnitude_history`, `trailing_wall_proximity/strength_trend_history`, `trailing_max_pain_drift_history` | TASK-005 | same |
| `trailing_cvd_magnitude_history`, `trailing_imbalance_magnitude_history`, `trailing_excursion_history` | TASK-006 | same |
| `trailing_average_range_history`, `trailing_gap_magnitude_history`, `trailing_basis_magnitude_history` | TASK-007 | same |
| `average_daily_volume: float` | TASK-006 | trailing average of prior sessions' final cumulative `volume` |
| `prior_day_high/low/close`, `prior_value_area: tuple[float,float,float] \| None` | TASK-007 | **most recent prior session_date's** final `signal_snapshots` row only (not a rolling window — yesterday's completed profile, specifically) |

**Reconstruction mechanism (the "formalize a proper mechanism" ask from TASK-006's ADR) — implemented as a small deterministic-string parse, not a contract change:** `template_reason`'s `context` dict is never handed back to the caller directly — the `SignalResult` 4-tuple only carries the *formatted string*, and changing that would touch the frozen contract binding across 5 already-merged modules for a benefit only this one caller needs. Instead, `engine.py` recovers it with a small regex against `template_reason`'s own deterministic `"... [key=val, key2=val2]"` suffix (guaranteed stable by constraint #3), then stores the recovered dict as **structured JSON** in `raw_readings`, not just the formatted string. Concretely: `raw_readings["order_flow"]["volume_participation_range"] = {"raw_value": ..., "reference_band": [...], "sub_score": ..., "context": {"established_low": 24400.0, "established_high": 24600.0}}`. `EngineState.load()` and the same-cycle `established_range` update both read this structured field back out — the *string* is only ever parsed once, at the call site, immediately after the function returns; nothing downstream ever re-parses a reason string. This resolves TASK-006's flagged gap without touching the `SignalResult` 4-tuple contract at all.

**Cross-session growing lists are reconstructed by querying, not by storing the whole list in every row.** Each row's `raw_readings` only ever holds *that cycle's* value (e.g. one `cvd_delta` reading); `EngineState.load()` queries all of today's rows (mid-session restart) or the last N session_dates' final rows (cross-session trailing history) and rebuilds the `list[float]` in memory. This avoids redundantly persisting an ever-growing list inside every single row.

**Initial `market_state` on a brand-new session:** defaults to `NO_GO` (the conservative "sit out" default, consistent with the system's whole premise) rather than carrying over yesterday's closing state — a new session starts from equilibrium, not from where yesterday's tape happened to end. On a mid-session process restart, `EngineState.load()` seeds `market_state` from today's most recent row instead.

### 5. Hysteresis: N-cycle confirmation (spec's first named mechanism, not margin-crossing)

Spec §7 allows either "N consecutive computation cycles" or "the composite must cross by a defined margin." **Picking N-cycle confirmation only, not both** — simplicity first; a single mechanism is easier to reason about and to write a "provably prevents flapping" test against (directive's named edge case) than two interacting ones. Margin-crossing is flagged as an alternative, not implemented for v1.

```python
proposed_state = state_for_score(composite_score, config.thresholds)   # pure function of this cycle's score alone

if proposed_state == state.pending_state:
    state.pending_streak += 1
else:
    state.pending_state = proposed_state
    state.pending_streak = 1

if state.pending_streak >= config.confirmation_cycles and proposed_state != state.confirmed_state:
    # flip: write state_transitions, update confirmed_state
    ...

market_state_to_persist = state.confirmed_state   # signal_snapshots always stores the CONFIRMED state, never the flickering proposed one
```
A composite oscillating exactly at a threshold produces a `proposed_state` that keeps resetting `pending_streak` back to 1 every time it crosses back — it can never reach `confirmation_cycles` consecutive agreement, so it never flips. This is the concrete mechanism the directive's edge case test needs to exercise.

### 6. `system_status` — passed through, never computed into `market_state`

`IngestionSnapshot.system_status` flows into the `signal_snapshots` row verbatim, alongside whatever `market_state` the composite happened to compute from whatever data was actually available that cycle (already degraded gracefully per §2/§3 above, not specially gated here). No `if system_status != "OK": force NO_GO` — that would be exactly the per-signal-veto pattern constraint #1 forbids, just at the feed level instead of the sub-signal level.

### 7a. `expected_move_consumed_ratio`'s missing data source — found and resolved before wiring `engine.py`

**Same "checked before designing, not guessed" discipline as TASK-006's ingestion-gap finding, this time surfaced during implementation rather than while drafting:** TASK-003's `expected_move_consumed_ratio` — spec's own "highest-value single signal" in the volatility category — takes `straddle_implied_expected_move: float | None` as a caller-supplied argument, but nothing in `IngestionSnapshot`/`OptionStrike` can produce it. `OptionStrike` has `call_oi`/`put_oi`/`call_iv`/`put_iv`/greeks only — no premium field (`call_ltp`/`put_ltp`) to build an actual ATM straddle price from. TASK-005 already declined adding option premium fields for a different function (`oi_buildup_classification`); this is the same missing data resurfacing for an unrelated reason.

**Human-confirmed 2026-07-03: VIX-based approximation, not a new ingestion amendment.** `india_vix` is already on `IngestionSnapshot` (TASK-003's own earlier amendment) — a standard practitioner shortcut converts it into an expected move without needing option premium at all:
```python
straddle_implied_expected_move = spot_ltp * (india_vix / 100) * math.sqrt(1 / 252)
```
**Deliberately a constant full-trading-day figure, not decayed by elapsed session time** — an earlier version of this idea considered scaling by `sqrt(fraction_of_session_elapsed)` (a more theoretically precise intraday-shrinking expected move), but that requires `engine.py` to compute "how much of the session has passed," which is a clock-time read this repo's constraint #2 exists to keep out of anything upstream of the scheduler. Using the full one-day figure as a constant denominator avoids that entirely: `expected_move_consumed_ratio`'s numerator (realized move so far) naturally grows through the session while the denominator stays fixed, so the ratio still rises across the day exactly as the spec's "is the day delivering on what premium priced in" framing wants — without `engine.py` ever asking what time it is.

`session_reference_price` is the session's opening **spot** price — a new, separate `EngineState` field, **not** reused from `session_open` (TASK-007's field is explicitly the opening **futures** price, used for gap classification against futures-denominated value areas; spot and futures differ by `futures_basis`, conflating them would be a subtle bug). Same capture discipline: caller-held, set to the first non-`None` `spot_ltp` of the session, cleared by `end_session()` alongside the rest of the session-scoped table in §4/§7.

### 7. End-of-session cleanup — in-memory only, `signal_snapshots` is never pruned

**Human-confirmed 2026-07-03, clarified after flagging a real conflict:** the concern was unbounded accumulation of data that's genuinely useless later, not a request to delete anything from Supabase. Resolved without touching Build Prompt 1's hard constraint (`signal_snapshots` must keep every raw per-cycle reading forever, so the composite is retroactively recomputable if weights change):

- **Nothing is ever deleted from Supabase.** `signal_snapshots` stays a true append-only log, permanently. This was never actually violated by what the user wanted — see below.
- **What actually accumulates uselessly is in-memory, not in Supabase, and this ADR already scoped it correctly in §4:** the "cumulative data" the user described (`cvd_delta_history`, `price_history`, `cycle_price_volume_history`, `established_range`, `session_open`, `session_open_max_pain`, `basis_history` — the whole "Session-scoped" table above) lives only in `EngineState`, in the running Python process. Left to grow "lazily reset at next day's first cycle detects a new session," these lists would sit fully populated in RAM for the ~18 idle hours between close and the next pre-market run, for no benefit.
- **Fix: an explicit `Engine.end_session()` call, scheduler-triggered at market close (3:31pm IST), not lazily inferred.** TASK-013 (scheduler) calls this once, right after the live loop's last cycle for the day — this is a clock-triggered *invocation*, which is fine per constraint #2 (the scheduler deciding *when* to run something is infra, not signal interpretation; `engine.py` itself still never reads a clock). `end_session()` clears every field in the "Session-scoped" table to its empty/`None` initial value. Nothing is written to Supabase by this call — the fields it clears were never persisted as growing lists in the first place (per §4's "reconstructed by querying, not stored redundantly" design), so there is nothing to synchronize.
- **`signal_snapshots` was never at risk of holding "useless" data to begin with:** it stores only the already-distilled `raw_readings`/`sub_scores`/`composite_score`/`reasons` per cycle — never a raw `IngestionSnapshot` dump (no `option_chain`, no `depth`, no per-tick payloads). Anything re-fetchable from Dhan's API was never being persisted here in the first place, so the "or can be fetched from API later again" half of the original ask is already satisfied by the existing schema (`docs/DATA_MODEL.md`), not something this ADR needs to add.

```python
# src/aeolus/engine/engine.py
def end_session(self) -> None:
    """Called once by TASK-013 at market close. Clears session-scoped
    EngineState fields only -- never touches Supabase. Cross-session fields
    (trailing histories, prior-day context) are untouched here; they're
    re-seeded fresh at the next start() call from what's already durable."""
    self._state.previous_snapshot = None
    self._state.cvd_delta_history = []
    self._state.price_history = []
    self._state.basis_history = []
    self._state.established_range = None
    self._state.cycle_price_volume_history = []
    self._state.session_open = None
    self._state.session_reference_price = None
    self._state.session_open_max_pain = None
```

## Component Boundaries

| File | Responsibility |
|------|---|
| `config/tuning.py` | `EngineConfig`/`CategoryWeights`/`StateThresholds` — `pydantic_settings.BaseSettings` schema |
| `config/profiles.py` | `EXPIRY_CONFIG`, `NON_EXPIRY_CONFIG` — two complete, hardcoded `EngineConfig` instances (ARES `config_profiles.py` pattern) |
| `src/aeolus/engine/state.py` | `EngineState` dataclass, `EngineState.load(client, session_date)` seeding query logic |
| `src/aeolus/engine/scorer.py` | `_safe_call`, category aggregation, composite calc, `state_for_score` |
| `src/aeolus/engine/engine.py` | `Engine.start(session_date)`, `Engine.run_cycle(snapshot)`, `Engine.end_session()` — public entrypoints TASK-013 calls |

## API Contracts

```python
# src/aeolus/engine/engine.py
class Engine:
    def __init__(self, supabase_url: str, supabase_key: str) -> None: ...

    def start(self, session_date: date) -> None:
        """Seeds EngineState from Supabase (cross-session trailing histories,
        prior-day context fields, and — on a same-day restart — session-scoped
        state from today's own rows). Loads both EngineConfig instances.
        Raises on invalid config (fail-fast, never partially starts)."""

    def run_cycle(self, snapshot: IngestionSnapshot) -> SignalSnapshot:
        """One computation cycle: calls all 16 sub-signal functions (via
        _safe_call), aggregates to composite, applies hysteresis, writes
        signal_snapshots (always) and state_transitions (only on a confirmed
        flip), updates EngineState in place, returns the written SignalSnapshot.
        Never raises on a sub-signal failure -- only on a write failure."""

    def end_session(self) -> None:
        """Called once by TASK-013 at market close (3:31pm IST). Clears
        session-scoped EngineState fields only -- see Decision §7. Never
        touches Supabase; signal_snapshots is never pruned."""

# src/aeolus/engine/scorer.py
def state_for_score(composite_score: float, thresholds: StateThresholds) -> MarketState: ...

def _safe_call(name: str, fn: Callable[..., SignalResult], *args: object, **kwargs: object) -> SignalResult: ...

# src/aeolus/engine/state.py
@dataclass
class EngineState:
    # session-scoped
    previous_snapshot: IngestionSnapshot | None
    cvd_delta_history: list[float]
    price_history: list[float]
    basis_history: list[float]
    established_range: tuple[float, float] | None
    cycle_price_volume_history: list[tuple[float, float]]
    session_open: float | None
    session_reference_price: float | None
    session_open_max_pain: float | None
    pending_state: MarketState
    pending_streak: int
    confirmed_state: MarketState
    # cross-session
    trailing_iv_history: list[float]
    trailing_spot_history: list[float]
    trailing_vix_history: list[float]
    trailing_iv_rising_magnitude_history: list[float]
    trailing_iv_falling_magnitude_history: list[float]
    trailing_gex_magnitude_history: list[float]
    trailing_pcr_roc_magnitude_history: list[float]
    trailing_wall_proximity_history: list[float]
    trailing_wall_strength_trend_history: list[float]
    trailing_max_pain_drift_history: list[float]
    trailing_cvd_magnitude_history: list[float]
    trailing_imbalance_magnitude_history: list[float]
    trailing_excursion_history: list[float]
    trailing_average_range_history: list[float]
    trailing_gap_magnitude_history: list[float]
    trailing_basis_magnitude_history: list[float]
    average_daily_volume: float | None
    prior_day_high: float | None
    prior_day_low: float | None
    prior_close: float | None
    prior_value_area: tuple[float, float, float] | None

    @classmethod
    def load(cls, client: Client, session_date: date) -> "EngineState": ...
```

## Implementation Amendment (2026-07-03) — gaps closed while writing code

**1. Cross-session trailing-history granularity, underspecified in the Decision section above:** every `trailing_*_history` field is seeded as **one value per prior trading day** (that day's chronologically final row), not one value per intraday cycle across many days. This wasn't explicitly settled in the Decision draft — resolved by extending TASK-003's own already-established convention (`trailing_iv_history` is explicitly "20-60 *sessions*") to every other trailing list, for consistency and because it keeps `EngineState.load()` to one query shape (last N distinct `session_date`s, final row each) instead of a much larger per-cycle scan.

**2. `previous_snapshot` is always `None` immediately after a restart, never reconstructed — accepted limitation, not a bug.** TASK-005's four functions need the actual previous `IngestionSnapshot` (specifically its `option_chain`), which is never persisted to `signal_snapshots` by design (no raw ingestion payloads, per §7). A mid-session engine restart therefore costs exactly one cycle of those four functions degrading to their normal insufficient-data path before `previous_snapshot` is populated again from live data — the same graceful path they already take on any session's genuine first cycle, not a new failure mode.

**3. Concrete `_carry` schema (the structured-JSON side of §4's mechanism):** `volatility._carry = {current_iv, spot_ltp, india_vix}`; `order_flow._carry = {cvd_delta, futures_ltp, futures_basis, volume, established_range_low, established_range_high}`; `context._carry = {futures_ltp, volume_delta, spot_ltp, day_high, day_low, close, poc, va_low, va_high}`; `oi_structure._carry = {max_pain}`. Each is engine-authored (never written by the signal modules themselves), keys omitted when `None` rather than stored as JSON `null`.

**4. `trigger_categories` on a `state_transitions` row** — not specified anywhere in the spec or directive at the level of a concrete rule. Implemented as the simplest defensible reading: every category whose score deviates from neutral by at least `0.1` (`abs(category_score - 0.5) >= 0.1`), regardless of direction. Flagging as a placeholder heuristic, not a calibrated one — reasonable first cut for Discord's eventual "driven by X, Y" phrasing (TASK-011), revisit if it proves too noisy or too quiet in practice.

**5. `dte` sentinel:** `SignalSnapshot.dte` is a non-nullable `int` (TASK-001 schema), but `dte()` returns `None` when `expiry_date` is missing (e.g. before `IngestionService.start()` has resolved it). Engine writes `-1` in that case — an out-of-band sentinel, never a fabricated real DTE value, chosen over changing TASK-001's schema for a startup-only edge case.

## Blocking Dependencies

1. **RESOLVED — aggregation formula, human-confirmed:** equal-weighted average per category, 5 config weights only.
2. **RESOLVED — state ownership, human-confirmed:** `engine.py` owns the Supabase client and all seeding/persistence; TASK-013 stays pure orchestration.
3. **RESOLVED — config pattern, human-confirmed to match ARES exactly:** `pydantic-settings` + two hardcoded `EngineConfig` instances in `config/profiles.py` (`EXPIRY_CONFIG`/`NON_EXPIRY_CONFIG`), not YAML files loaded at runtime. Values themselves are still judgment-calibrated placeholders, not backtested (no `outcome_labels` data exists yet, per Open Decision #3's live-forward-only resolution) — needs human sign-off on the specific starting numbers when `config/profiles.py` is actually authored during implementation, same as every calibration knob in TASK-003..007.
4. **RESOLVED — end-of-session cleanup scope, human-confirmed after flagging the Build Prompt 1 conflict:** in-memory `EngineState` only, via `Engine.end_session()` at market close. `signal_snapshots` is never pruned — see Decision §7.
5. **`lot_size` sourcing** — confirmed already available via `IngestionService.lot_size` (TASK-002), passed to `gex_regime` at call time by `engine.py`. No new dependency.

## Performance / Failure Modes

- **Startup seeding cost:** one Supabase query per cross-session trailing history field (or one combined query fetching N prior sessions' rows and fanning out client-side) — a fixed, once-per-session cost, not per-cycle. Flagging as a place to batch into fewer queries during implementation if the row count makes it slow, not an ADR-level concern.
- **A crashing sub-signal never crashes a cycle** — `_safe_call` isolates it to a neutral reading for that one sub-signal only (§3).
- **A failing Supabase write does propagate** — unlike a sub-signal bug, a failed `signal_snapshots` insert means the cycle's result is lost entirely; `run_cycle` raises rather than silently dropping data, since silently continuing would desync `EngineState` from what's actually durable.
- **First session after go-live:** every cross-session field seeds to `None`/empty (no prior rows exist) — already the exact input shape every TASK-003..007 function's own insufficient-data path expects; no new fallback needed here.

## Definition of Done

- [x] Aggregation test: constructed per-category sub_score sets produce the documented equal-weighted category averages and weighted composite
- [x] `_safe_call` test: a sub-signal function raising an exception degrades to `(None, band, 0.5, "...: error (...)")`, does not propagate, and the cycle still completes
- [x] Partial-composite test: an entire category all-`None`/`0.5` still contributes at its configured weight (not excluded, not renormalized)
- [x] Hysteresis test: a composite oscillating exactly across a threshold every cycle never accumulates `confirmation_cycles` consecutive agreement — `market_state` never flips, `state_transitions` never written (directive's named edge case, provably not flapping)
- [x] Hysteresis test: `confirmation_cycles` consecutive agreement on a new proposed state does flip, writes exactly one `state_transitions` row
- [x] Config test: `EXPIRY_CONFIG`/`NON_EXPIRY_CONFIG` construction is the validation — a deliberately malformed `EngineConfig(...)` (missing field, weights not summing to 1.0) raises at construction time, not later
- [x] `EngineState.load()` test (live, against real Supabase): seeds cross-session trailing histories and prior-day fields correctly from constructed prior rows; first-session-ever (no rows) seeds to empty/`None` without raising
- [x] `end_session()` test: clears every session-scoped field to its empty/`None` initial value; cross-session fields (trailing histories, prior-day context) are untouched; no Supabase call made
- [x] Constraint check: no per-signal veto (aggregation is the only path to `market_state`, §2/§6 explicit), no clock-time branching (`dte()` reused, not recomputed; `end_session()`'s market-close trigger is the scheduler's job, `engine.py` itself never reads a clock), deterministic reasons (reused `template_reason`, `context` now also persisted structurally), `system_status` never mapped into `market_state` (§6)

**Implemented:** 2026-07-03. See `reports/debug/TASK-008_debug-report.md`, `reports/qa/TASK-008_qa-report.md`.
