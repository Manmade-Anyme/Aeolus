# Debug Report — TASK-009

**Date:** 2026-07-03
**Verdict:** ✅ CLEAN

## What was run

- `python -m pytest tests/ -q` — full suite (188 passed, was 171 before this task), including new live integration tests against real Supabase (`test_generator_integration.py`)
- `python -m ruff check src/ tests/ config/`
- `python -m mypy src/aeolus config/`

## Observed behavior

**Two gaps found by checking the actual code/schema before designing, not the directive's wording:**
1. The directive's claimed data source ("TASK-003's separate pre-market function" for straddle premium level) doesn't exist — `volatility.py` only ever had the live `expected_move_consumed_ratio`, and `OptionStrike` still has no premium field. Resolved by reusing the VIX-based approximation already human-approved for TASK-008, promoted into a new shared `volatility.implied_expected_move()` function (additive-only amendment — `Engine.run_cycle` refactored to call it instead of its own inlined copy, no behavior change).
2. `daily_outlook`'s schema (already merged) has no columns for the 7-way probability distribution or a secondary call the spec describes. Resolved by packing both into the existing `contributing_inputs` jsonb column — no migration.

**New design surface — archetype scoring is genuinely new, not a reuse of an established formula.** Implemented as a uniform-prior-plus-multiplicative-nudges model (`outlook/archetype.py`), explicitly flagged in the ADR as the piece most likely to need real revision once `outcome_labels`/`realized_archetype` data exists to check it against.

**One real gap closed during implementation, documented as an ADR amendment:** the upsert needed to reuse the existing row's primary key (looked up by `session_date` first) rather than let a freshly-constructed model's random `id` rotate the row's PK on every re-run.

**Deliberate non-reuse, explained in the ADR rather than left implicit:** `TASK-007`'s `gap_classification` was NOT reused for the outlook's "futures price context" input — that function is designed for the live gap-and-go/gap-and-fill read, which needs price movement after the session opens (doesn't exist pre-market). A separate, simpler descriptive gap figure was computed instead.

## Issues

| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| — | — | None found | — | — |

## Constraint audit

- [x] No per-signal veto — n/a, the Outlook is a forecast prior, never NO-GO/PREPARE/GO; verified `predicted_archetype` is always one of the 7 `DayArchetype` values, never a state
- [x] No clock-time branching — `archetype.py`/`generator.py` never call `datetime.now()`; `dte` is a passed-in value from `context.dte()`, reused verbatim
- [x] Deterministic — every number in `contributing_inputs` traces to a named, inspectable input (`archetype_distribution`, `secondary_archetype`, `oi_max_pain_carryover`, etc.), no free-text narration anywhere
- [x] Polarity — n/a, the Outlook doesn't use GO/NO-GO vocabulary at all (Spec §5.1 explicit requirement), verified by construction (no such literals appear in `outlook/`)
- [x] `trend_exhaustion_flag` is its own typed column, never folded only into `contributing_inputs` — matches both Spec §5.1 and the TASK-001 schema's explicit design for this
