# Architecture Decision Record — TASK-009

**Directive:** `directives/TASK-009_premarket-outlook.md`
**Status:** APPROVED (2026-07-03)
**Date:** 2026-07-03

## Problem

Single-run, pre-open job producing Spec §5.1's probabilistic day-archetype forecast: yesterday's trend-exhaustion check as the headline (ahead of every other input), a probability distribution across the 7 archetypes (Spec §4), a primary/secondary call, a confidence measure — written once to `daily_outlook`. Forecast-prior vocabulary only, never NO-GO/PREPARE/GO.

**Two gaps found by checking the actual code/schema, not the directive's wording, before designing:**

1. **The directive's claimed data source doesn't exist.** It says the ATM straddle premium level (vs 10-20 session history) comes "from TASK-003's separate pre-market function" — no such function exists in `volatility.py`; TASK-003 only ever built the *live* `expected_move_consumed_ratio`. The spec text itself is more honest about this: "the pre-market equivalent... lives in the Pre-Market Outlook, Section 5.1" — i.e., this ADR's job, not TASK-003's. And the same premium-data gap TASK-008 already hit resurfaces here: `OptionStrike` still has no `call_ltp`/`put_ltp`, so true straddle premium can't be computed either way.
2. **`daily_outlook`'s schema (TASK-001, already merged) has no room for the output Spec §5.1 actually describes.** `predicted_archetype`/`archetype_confidence` are single scalars; there's no column for a 7-way probability distribution or a secondary call.

**Human-confirmed 2026-07-03:**
1. Reuse the VIX-based expected-move approximation already approved for TASK-008 (`spot_ltp * (india_vix/100) * sqrt(1/252)`), factored out into a shared function rather than duplicated a second time — see Decision §1.
2. No `daily_outlook` schema migration. `predicted_archetype`/`archetype_confidence` hold the primary call; the full 7-way distribution and secondary call live inside the existing `contributing_inputs` jsonb column, which was already designed to be a flexible bucket.

**Inherited, already-flagged, not re-opened here:** `gift_nifty` is structurally `None` in every `IngestionSnapshot` (TASK-002 ADR: confirmed absent from Dhan API v2; a live `SECURITY_ID=5024` finding was explicitly scoped out of a prior amendment by human decision). "GIFT Nifty gap" therefore degrades to the same insufficient-data path as every other `None` input in this ADR suite — this ADR doesn't reopen that question.

## Decision

### 1. `implied_expected_move` — promoted into `volatility.py`, a small TASK-003 amendment

The VIX-based approximation from TASK-008 §7a is needed a second time here. Rather than copy the formula, it becomes a new, small, pure function in `src/aeolus/signals/volatility.py` (additive only — no existing signature changes, nothing else in that module touched):
```python
def implied_expected_move(spot_ltp: float | None, india_vix: float | None) -> float | None:
    """spot_ltp * (india_vix/100) * sqrt(1/252) -- one trading day's VIX-implied
    expected move. Constant, not decayed by elapsed session time (same reasoning
    as TASK-008 ADR §7a: avoids any caller needing to read a clock). None if
    either input is missing."""
```
`Engine.run_cycle` (TASK-008) is refactored to call this instead of its own inlined copy — one formula, one place, matching the human-confirmed decision to reuse rather than duplicate.

**`straddle_level_vs_history` (daily_outlook's own dedicated column):**
```python
today_implied_move = implied_expected_move(snapshot.spot_ltp, snapshot.india_vix)
straddle_level_vs_history = _percentile_rank(today_implied_move, trailing_implied_move_history)
```
`trailing_implied_move_history` is `implied_expected_move` computed from each of the last 10-20 prior sessions' final `IngestionSnapshot`-equivalent readings (spot/VIX), same seeding style as every other trailing history in this suite. Empty history → `0.5` (neutral), same convention as `_percentile_rank` everywhere else — satisfies the directive's "first session, no prior-day data" edge case for this specific input without a separate code path.

### 2. Pre-market seeding — a smaller, dedicated query, not `EngineState` reuse

TASK-009 explicitly does not depend on TASK-008 (directive: "TASK-008 not required"). Even though TASK-008 now exists, `outlook/generator.py` does **not** reach into `EngineState` — that class is engine-internal, seeded for a continuous live loop's needs (16 sub-signals' worth of state), most of which the Outlook has no use for. Instead, `outlook/` queries exactly what it needs, once, from the most recent prior `session_date`'s final `signal_snapshots` row (same "last row of the most recent prior day" query shape TASK-007/008 already established, just narrower):
- `context` category: `profile_shape` (`"trend"`/`"balanced"`/`None`), and `_carry`'s `day_high`/`day_low`/`close`/`poc`/`va_low`/`va_high`
- `oi_structure` category: `_carry`'s `max_pain`, and `pcr_level_and_roc`'s `context.pcr_level`
- last 10-20 prior sessions' final rows: `volatility._carry`'s `current_iv`/`spot_ltp`/`india_vix` (to rebuild `trailing_iv_history`/`trailing_vix_history`/`trailing_implied_move_history` — the same three cross-session lists TASK-008 already builds, computed once more here rather than shared, since this is a separate process/run, not a shared in-memory object)

A small, deliberate duplication of query shape against TASK-008's `EngineState._seed_cross_session` — accepted because the two modules run at different times for different purposes (one continuous process, one single pre-open invocation) and coupling them would be a worse trade than the ~20 lines of duplicated query logic.

### 3. Archetype scoring — the biggest judgment call in this ADR, flagged for explicit sign-off

No existing signal module produces "archetype probabilities" — every input here is a genuinely new heuristic, not a reuse of an established formula. Starting from a **uniform prior** (`1/7` each of the 7 `DayArchetype` values) and applying independent, bounded multiplicative nudges per available input, then renormalizing — the same "start from judgment, refine once `outcome_labels` exist" philosophy already used for every threshold/weight in this suite (Open Decision #2's rejection of graduated DTE weighting used the identical reasoning). A multiplicative model keeps each nudge auditable in isolation (unlike an additive score that can produce impossible values), and degrades safely to the uniform prior when every input is missing (first-session edge case).

```python
scores = {archetype: 1.0 for archetype in DayArchetype}   # uniform prior

# --- headline driver: yesterday's trend exhaustion (Spec §5.1's stated lead line) ---
if profile_shape == "trend":
    scores["clean_trend"] *= 0.5          # less likely to repeat an already-exhausted explosive trend
    scores["grinding_trend"] *= 1.5       # tired continuation
    scores["pinned_range"] *= 1.5         # full stall
elif profile_shape == "balanced":
    scores["breakout_transition"] *= 1.2  # a range that's been building is more likely to resolve
    scores["double_distribution"] *= 1.2  # or continue its rotational character
# profile_shape is None (first session) -> no nudge, stays uniform

# --- volatility-heading-in: IV percentile / VIX / straddle level, same direction, averaged ---
expanding_vol_pct = average of whichever of {iv_percentile_rank, vix_level_and_roc,
                                             straddle_level_vs_history} are not None
                    (all None -> no nudge, stays uniform)
if expanding_vol_pct > 0.7:
    scores["clean_trend"] *= 1.3; scores["choppy_range"] *= 1.3; scores["event_gap"] *= 1.3
elif expanding_vol_pct < 0.3:
    scores["grinding_trend"] *= 1.3; scores["pinned_range"] *= 1.3

# --- DTE: pinning intensifies into expiry (Spec §8) ---
if dte == 0:
    scores["pinned_range"] *= 1.3

# --- GIFT Nifty gap magnitude (structurally None in v1 -- see Problem) ---
if gift_nifty_gap is not None and abs(gift_nifty_gap) > gift_gap_threshold:
    scores["event_gap"] *= 1.5; scores["clean_trend"] *= 1.2

total = sum(scores.values())
distribution = {name: value / total for name, value in scores.items()}
primary, primary_prob = max(distribution.items(), key=lambda kv: kv[1])
secondary, secondary_prob = second-highest of distribution
```
**Flagging every nudge factor (0.5/1.2/1.3/1.5) and every threshold (0.7/0.3, `gift_gap_threshold`) as judgment placeholders, same bar as every calibration knob in TASK-003..008** — not backtested, revisit once `outcome_labels`/`daily_outlook.realized_archetype` (TASK-012) accumulate enough history to check whether any of this actually predicts real day-types. **This entire scoring function is the part of this ADR most likely to need real revision once data exists** — flagged explicitly rather than presented as settled.

### 4. Futures price context — a plain descriptive gap, not `gap_classification` reuse

TASK-007's `gap_classification` is designed for the *live* gap-and-go/gap-and-fill read (needs price movement after the session open, which doesn't exist yet pre-market). Reusing it here would just always return its "still at the open" case, adding no information. Instead: a simple, purpose-built descriptive figure, not re-scored into the archetype model directly — surfaced in `contributing_inputs` for a human/Discord reader:
```python
futures_gap = snapshot.futures_ltp - prior_close
inside_prior_value_area = prior_value_area is not None and va_low <= snapshot.futures_ltp <= va_high
```

### 5. `max_pain_drift` and PCR carryover — reuse, not reinvention

`oi_structure.max_pain_drift` (TASK-005, already built) is called as-is: `max_pain_drift(snapshot, prior_max_pain, trailing_max_pain_drift_history, reference_band)`, where `prior_max_pain` is yesterday's carried-over `oi_structure._carry.max_pain`. This is a genuine reuse, not a new formula — max pain drift since yesterday's close is exactly what this function already computes. PCR itself is **not** re-scored (would need yesterday's full `option_chain` for the ROC calculation, which — same accepted limitation as TASK-008 §Implementation-Amendment-2 — isn't persisted); yesterday's closing PCR *level* is surfaced as a plain descriptive number from `oi_structure.pcr_level_and_roc`'s stored `context.pcr_level`, not recomputed.

### 6. Idempotency — upsert on `session_date`, not insert-or-fail

`daily_outlook.session_date` is unique (TASK-001 schema). A duplicate pre-open run (scheduler restart, manual re-trigger) **upserts** rather than raising or silently skipping — the latest computation before market open should win, consistent with this being a "single-run pre-open job" in intent, not a strict single-attempt one. `client.table("daily_outlook").upsert(payload, on_conflict="session_date").execute()`.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/signals/volatility.py` | *(amended)* new `implied_expected_move` function, additive only |
| `src/aeolus/engine/engine.py` | *(amended)* `run_cycle` calls `implied_expected_move` instead of its own inline copy |
| `src/aeolus/outlook/archetype.py` | Pure scoring: `score_archetypes(...) -> dict[DayArchetype, float]`, `primary_and_secondary(distribution)` |
| `src/aeolus/outlook/generator.py` | `OutlookGenerator` — seeds prior-day context (own dedicated query), computes every input, calls `archetype.py`, upserts `daily_outlook` |

## API Contracts

```python
# src/aeolus/signals/volatility.py
def implied_expected_move(spot_ltp: float | None, india_vix: float | None) -> float | None: ...

# src/aeolus/outlook/archetype.py
def score_archetypes(
    profile_shape: str | None,               # "trend" | "balanced" | None
    expanding_vol_pct: float | None,          # averaged IV-percentile/VIX/straddle-level read, None if all missing
    dte: int | None,
    gift_nifty_gap: float | None,
    gift_gap_threshold: float,
) -> dict[str, float]:
    """Returns a normalized probability distribution over the 7 DayArchetype
    values. Every input independently optional -- all-None degrades to the
    uniform 1/7 prior."""

def primary_and_secondary(distribution: dict[str, float]) -> tuple[str, float, str, float]:
    """(primary_archetype, primary_prob, secondary_archetype, secondary_prob)."""

# src/aeolus/outlook/generator.py
class OutlookGenerator:
    def __init__(self, supabase_url: str, supabase_key: str) -> None: ...

    def run(self, session_date: date, snapshot: IngestionSnapshot) -> DailyOutlook:
        """Single pre-open invocation. Seeds prior-day context + trailing
        histories from Supabase, computes every Spec §5.1 input, scores
        archetypes, upserts exactly one daily_outlook row (on_conflict=
        session_date), returns it."""
```

## Blocking Dependencies

1. **RESOLVED — straddle-level data source, human-confirmed:** reuse the VIX-based `implied_expected_move`, promoted into `volatility.py` (TASK-003 amendment, additive only).
2. **RESOLVED — `daily_outlook` schema, human-confirmed:** no migration. Distribution + secondary call packed into `contributing_inputs`.
3. **Archetype-scoring nudge factors and thresholds** — flagged in Decision §3 as judgment placeholders needing explicit sign-off, same bar as every calibration knob in TASK-003..008, and the part of this ADR most likely to need real revision once `outcome_labels` data exists.
4. **`gift_gap_threshold`** — a new calibration knob (points), config-sourced like every other threshold in this suite. Currently moot in practice (`gift_nifty` is structurally `None`), kept for whenever that changes.

## Performance / Failure Modes

- **No caching or state in `archetype.py`** — pure function of its arguments, same discipline as every prior signal module.
- **First session after go-live:** every prior-day input is `None`/absent → `score_archetypes` receives all-`None` → uniform distribution, `trend_exhaustion_flag=False`, `straddle_level_vs_history=0.5`. No crash, no fabricated read.
- **GIFT Nifty unavailable pre-open (directive's named edge case):** already the steady-state in v1 (`gift_nifty` is structurally `None`) — `gift_nifty_gap` is `None`, that nudge never fires, same graceful path as "first session."
- **Duplicate run same session (directive's named edge case):** handled by upsert, §6 — never a constraint-violation crash, never a silent duplicate row.

## Implementation Amendment (2026-07-03) — one gap closed while writing code

**`OutlookGenerator.run()`'s upsert needed the existing row's primary key, not just `on_conflict`.** Building the upsert payload from a freshly-constructed `DailyOutlook` model means a new random `id` (pydantic's `default_factory=uuid4`) every call. Naively upserting that would rotate the row's primary key on every re-run — harmless to no FK today, but sloppy and a latent risk if TASK-012 ever gains a reason to reference `daily_outlook.id`. Resolved: before upserting, look up the existing row's `id` by `session_date` and reuse it when present, so a duplicate run genuinely updates in place rather than replacing the primary key underneath an unchanged logical row.

## Definition of Done

- [x] `implied_expected_move` test: correct formula, `None` on missing `spot_ltp`/`india_vix`
- [x] `score_archetypes` test: `profile_shape="trend"` shifts probability mass toward `grinding_trend`/`pinned_range` and away from `clean_trend` (vs. the uniform baseline); `profile_shape="balanced"` shifts toward `breakout_transition`/`double_distribution`; all-`None` inputs produce the exact uniform `1/7` distribution
- [x] `primary_and_secondary` test: correct ranking on a constructed distribution, including a tie-breaking case
- [x] `OutlookGenerator.run()` test (live, against real Supabase): writes one `daily_outlook` row with `predicted_archetype`/`archetype_confidence` matching the primary call, `trend_exhaustion_flag` matching yesterday's profile shape, `straddle_level_vs_history` a valid `0.0-1.0`, distribution+secondary packed into `contributing_inputs`
- [x] Idempotency test (live): running `run()` twice for the same `session_date` upserts, never raises, never produces two rows
- [x] First-session test (live, no prior rows): `run()` completes without raising, uniform distribution, `trend_exhaustion_flag=False`
- [x] Constraint check: no per-signal veto (n/a — a forecast prior, not a state), no clock-time branching (nothing here reads wall-clock time; DTE is a passed-in value from `dte()`), deterministic — forecast is not a `template_reason` string but every number in `contributing_inputs` traces to a named input, output vocabulary never uses NO-GO/PREPARE/GO

**Implemented:** 2026-07-03. See `reports/debug/TASK-009_debug-report.md`, `reports/qa/TASK-009_qa-report.md`.
