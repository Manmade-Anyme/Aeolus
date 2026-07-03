# Architecture Decision Record — TASK-012

**Directive:** `directives/TASK-012_outcome-backfill.md`
**Status:** APPROVED (2026-07-03)
**Date:** 2026-07-03

**Build-time corrections (found during implementation, not re-litigated, just documented honestly per this project's established practice):**
1. **0007's partial unique indexes didn't work.** PostgREST's upsert only ever emits `ON CONFLICT (columns)` with no `WHERE` clause, and Postgres only matches a partial index for conflict inference when the same predicate is repeated in the conflict clause. Confirmed live (`42P10: no unique or exclusion constraint matching the ON CONFLICT specification`). Fixed via `0008_fix_outcome_labels_idempotency_constraint.sql`: **plain** (non-partial) `UNIQUE (snapshot_id, horizon_minutes)` / `UNIQUE (transition_id, horizon_minutes)` constraints — standard SQL treats `NULL <> NULL` for uniqueness, so a plain constraint already ignores every row where that column is null, achieving the exact same guarantee without needing a partial predicate at all. Same precedent as TASK-001's 0006 fixing 0005 — 0007 is left as-applied, not edited.
2. **The directional axis doesn't reuse `context_signals.prior_day_profile_shape`** as originally proposed below — that function needs `trailing_average_range_history`, which is `EngineState`-internal in-memory data, never persisted to `signal_snapshots`, so a standalone DB-only job can't call it. Implemented instead as a small self-contained range/close-location helper in `realized_archetype.py`, needing nothing but this session's own stored `futures_ltp` readings. Same close-location-within-range intuition, no new formula category, just no cross-day-history dependency.

## Problem

An after-the-fact job that (1) writes `outcome_labels` rows joining forward realized outcomes (+15/+30/+60 min) onto `signal_snapshots`/`state_transitions`, and (2) backfills `daily_outlook.realized_archetype`. Must never run live/synchronously with the scoring loop (labels don't exist at signal-time) — this is a look-back batch job, invoked once per session after close (by a human, a cron, or later TASK-013's post-close trigger; TASK-013 doesn't exist yet, so this ADR doesn't depend on it).

**Two real gaps surfaced while writing this ADR, both flagged below for explicit approval:**
1. **No unique constraint exists on `outcome_labels` for idempotent re-runs.** `docs/DATA_MODEL.md`/migration 0005 has no `(snapshot_id, horizon_minutes)` uniqueness — re-running today's job would just insert duplicates. Needs a schema amendment (small, additive — same category of change as TASK-001's 0006 fix).
2. **`daily_outlook.realized_archetype` needs a realized-day classifier that doesn't exist anywhere.** Nothing in the codebase maps a *completed* session's actual price/IV action to one of the 7 `DayArchetype` values — TASK-009's `archetype.py` only *predicts* a distribution pre-market. This is the biggest genuinely new design surface in this ADR, same category as TASK-009's archetype-scoring model was in its own ADR.

## Decision

### 1. Outcome labels — reuse existing values, no new data collection

**`realized_move`** — real, observed: `target_snapshot.futures_ltp − t0.futures_ltp`, both already stored per-cycle in `raw_readings["order_flow"]["_carry"]["futures_ltp"]`.

**`straddle_price_change`** — this project already has an established stand-in for "straddle level": `volatility.implied_expected_move(spot_ltp, india_vix)`, used identically by TASK-008 (`expected_move_consumed_ratio`) and TASK-009 (`straddle_level_vs_history`) as the project's one accepted proxy for real straddle premium (never actually ingested — no real chain-derived premium is persisted per cycle, only extracted IV/spot). **Reusing it a third time here, not inventing a new formula**: `straddle_price_change = implied_expected_move(target.spot_ltp, target.india_vix) − implied_expected_move(t0.spot_ltp, t0.india_vix)`, using `spot_ltp`/`india_vix` already carried in `raw_readings["volatility"]["_carry"]`.

**`direction`** — `UP`/`DOWN`/`FLAT` from `realized_move` against a new `FLAT_THRESHOLD_POINTS = 15.0` module constant (NIFTY futures points). No existing config value fits (existing thresholds are percentile/percentage-based, not raw points) — flagging this as a placeholder judgment call, same treatment as TASK-009's nudge factors: revisit once `outcome_labels` accumulates enough history to check against realized volatility.

**Forward-match logic:** for a given `t0` (a snapshot's or transition's own `ts`), find the session's own snapshot whose `ts` is closest to `t0 + horizon_minutes`, accepting it only within a **±5 minute tolerance**. No match within tolerance → skip that horizon entirely (directive's "missing forward data" edge case) rather than fabricating a value from a stale or too-early reading. This same skip path naturally handles both stated edge cases without special-casing: a snapshot within 60 min of close has no valid +60 target (nothing exists past close) → skipped; a halted/shortened session has gaps in its own snapshot cadence → skipped wherever the gap makes a horizon unmatchable.

**Both `signal_snapshots` and `state_transitions` get labeled**, per Build Prompt 12's literal "back onto `signal_snapshots` / `state_transitions` rows" and the `OutcomeLabel` model's `snapshot_id`/`transition_id` dual-source design (already built, TASK-001). A transition's `t0` reuses the *snapshot* nearest its own `ts` for its `spot_ltp`/`india_vix`/`futures_ltp` inputs (a `StateTransition` row itself carries none of these — only `from_state`/`to_state`/`trigger_categories`/`reason`).

### 2. Schema amendment — idempotency via a real unique constraint, not application-level checking

New migration `0007_outcome_labels_idempotency.sql`:
```sql
create unique index if not exists uq_outcome_labels_snapshot_horizon
    on outcome_labels (snapshot_id, horizon_minutes) where snapshot_id is not null;
create unique index if not exists uq_outcome_labels_transition_horizon
    on outcome_labels (transition_id, horizon_minutes) where transition_id is not null;
```
Partial indexes (not a single composite unique, since a row has exactly one of the two source columns populated — a plain composite unique across both would allow duplicate `(snapshot_id, horizon)` pairs as long as `transition_id` differs, which isn't the guarantee needed). Enables a real `upsert(..., on_conflict="snapshot_id,horizon_minutes")` / `on_conflict="transition_id,horizon_minutes"` — true idempotency guaranteed by Postgres, not a race-prone "select existing, skip if present" in application code. Same category of change as TASK-001's 0006 fix (small, additive, applied by hand via the Supabase Dashboard SQL Editor per this project's established access pattern).

### 3. Realized-archetype classifier — the flagged design surface

Spec §3 already publishes the exact cross-table this classifier needs — Section 4's own "Directional × Volatility" framing is reused verbatim, not invented:

| Directional (this session) | Volatility (this session) | → Archetype |
|---|---|---|
| Trend | Expanding | `clean_trend` |
| Trend | Contracting | `grinding_trend` |
| Balanced/range | Contracting | `pinned_range` |
| Balanced/range | Expanding | `choppy_range` |

**Directional axis** — reuses `context_signals.prior_day_profile_shape`'s existing range-expansion + close-location-in-range logic, called with *today's own* session high/low/close/value-area/trailing-average-range in place of "yesterday's" (the function doesn't know or care whose day it's scoring — it's a pure range/close-location classifier). Zero new formula.

**Volatility axis** — sign of `last_snapshot.current_iv − first_snapshot.current_iv` (both already stored in `raw_readings["volatility"]["_carry"]`). Positive → expanding, negative/zero → contracting. Simplest defensible read; no new trailing-history dependency (that's for cross-day percentile ranks, not a same-day open-vs-close comparison).

**`breakout_transition` override** — detected by independently classifying the session's first half vs second half (split by snapshot count, not clock time — constraint #2) using a small self-contained helper (range + close-location-within-range over each half's own `futures_ltp` readings, not reusing `prior_day_profile_shape` directly since that function's signature expects cross-day inputs a same-day half-split doesn't have). If first half reads `balanced` and second half reads `trend` → overrides the quadrant result to `breakout_transition` (Range→Trend, exactly Spec §4's #5 definition).

**`event_gap` override** — detected when (a) the session's first cycle's `gap_classification` raw_value is non-zero (opened beyond prior value area) **and** (b) `current_iv` peaks mid-session above both its opening and closing values (spike-then-crush, Spec §4's #6 "typical premium behavior" wording). Both conditions read from already-stored per-cycle data, no new inputs.

**`double_distribution` — explicit known limitation, not detected.** Spec §4 describes it as "looks trend-y on net change, isn't structurally," which needs a true bimodal volume-at-price read (two separate POCs) — `signal_snapshots` only ever persists the *current cumulative* POC/value-area per cycle (`raw_readings["context"]["_carry"]`), never the full histogram, and the histogram itself is never persisted (TASK-008's `EngineState.cycle_price_volume_history` is in-memory only, cleared at `end_session`). Detecting this reliably needs a full-session histogram to be stored somewhere — out of scope for this ADR. **Falls through to the 4-quadrant classification** (no override triggers) — an accepted gap, flagged exactly like TASK-009's `gift_nifty` always-`None` limitation, not silently pretended to work.

**Precedence when multiple overrides could apply:** `breakout_transition` checked first, then `event_gap`, else the 4-quadrant base result. Both overrides are rare/mutually-describing-different-sessions in practice; an explicit order avoids ambiguity rather than leaving it to dict-iteration or match-order accident.

**This entire classifier is flagged as the ADR's central judgment call — same footing as TASK-009's archetype-scoring nudge factors: unbacktested, revisit once enough `realized_archetype` history accumulates to check it against actually-traded outcomes.**

### Component Boundaries

| File | Responsibility |
|------|---|
| `supabase/migrations/0007_outcome_labels_idempotency.sql` | Original (non-working) partial unique indexes, left applied/unedited |
| `supabase/migrations/0008_fix_outcome_labels_idempotency_constraint.sql` | Working plain unique constraints PostgREST's upsert can actually target |
| `src/aeolus/jobs/backfill.py` | `OutcomeBackfillJob.run(session_date)` — outcome labels + realized-archetype backfill |
| `src/aeolus/jobs/realized_archetype.py` | The classifier described above, kept separate from the labeling logic (different responsibility, different test surface) |

## API Contracts

```python
class OutcomeBackfillJob:
    def __init__(self, supabase_url: str, supabase_key: str) -> None: ...

    def run(self, session_date: date) -> None:
        """Loads the session's signal_snapshots + state_transitions, upserts
        outcome_labels for every (entity, horizon) pair with a valid forward
        match, and upserts daily_outlook.realized_archetype. Idempotent via
        the 0007 unique indexes. Never raises on a single missing forward
        match -- that horizon is just skipped for that entity."""


def classify_realized_archetype(
    snapshots: list[SignalSnapshot],
) -> DayArchetype:
    """Pure function, no I/O. Empty/single-snapshot input -> the 4-quadrant
    base case computed from whatever's available (degrades gracefully, never
    raises)."""
```

## Performance / Failure Modes

- A session with very few snapshots (e.g. a halted session) still produces a best-effort realized-archetype read from whatever exists — never raises, degrades toward the base 4-quadrant case as overrides need more data than the base case does.
- Missing `daily_outlook` row for `session_date` (TASK-009 didn't run that day): `run()` skips the realized-archetype backfill step for that date (logs, doesn't raise) but still writes whatever `outcome_labels` it can from `signal_snapshots`/`state_transitions` — the two backfill steps are independent, one missing input doesn't block the other.
- Re-running `run()` for an already-backfilled date: every write is an upsert against the new unique indexes (labels) or the existing `daily_outlook` primary key (already TASK-009's idempotent-upsert pattern) — no duplicates, matches directive AC directly.

## Definition of Done

- [ ] Integration-style tests against `OutcomeBackfillJob.run()` (real Supabase, live inserts/upserts — same convention as TASK-008/009's live integration tests)
- [ ] Idempotency test: running `run()` twice for the same session_date produces the same row count, never raises, never duplicates
- [ ] Forward-match tolerance test: a horizon with no snapshot within ±5 min is skipped, not fabricated
- [ ] `classify_realized_archetype` unit tests covering all 4 quadrant combinations + both overrides + the double_distribution fallback (explicitly asserting it falls through, not that it's "handled")
- [ ] Constraint check: no per-signal veto (n/a), no clock-time branching (session split is by snapshot count, not wall-clock time), deterministic (classifier and forward-match are pure functions of stored data, no randomness/LLM), polarity n/a (this module doesn't score GO/NO-GO, it labels realized outcomes for later ML use)
