# TASK-009 Pre-Market Outlook Generator

**Goal:** Single-run pre-open job producing the Spec §5.1 probabilistic day-archetype forecast, written to `daily_outlook`, led by the prior-day trend-exhaustion check.

**Acceptance Criteria:**
- [x] ⭐ HEADLINE DRIVER: if yesterday's profile (TASK-007) resolved as clean/elongated trend day, this is the LEAD LINE of the output, ahead of all other inputs, raising the prior for digestion/consolidation (archetypes 2/3). This is the pattern the project exists to catch (Spec §1).
- [x] Inputs weighed: GIFT Nifty gap (structurally `None` in v1, inherited limitation), yesterday's profile shape, ATM straddle premium level vs 10–20 session history (via a VIX-based approximation — TASK-003's claimed function never existed, see ADR), IV percentile/VIX heading in, OI/max-pain carryover, futures price context, DTE
- [x] Output = archetype probability distribution + primary/secondary call + confidence — forecast-prior vocabulary, NEVER NO-GO/PREPARE/GO (packed into `contributing_inputs`, no `daily_outlook` schema change)
- [x] Writes exactly one `daily_outlook` row per session; trend-exhaustion flag stored as its own field
- [x] Realized-archetype backfill NOT here (that's TASK-012)

**Inputs:** TASK-002 (GIFT Nifty, futures), TASK-003 (`implied_expected_move`, new shared function), TASK-007 (profile flag, DTE); Spec §5.1, Build Prompt 9.

**Output:** `src/aeolus/outlook/`.

**Edge Cases:** GIFT Nifty unavailable pre-open; first session (no prior-day data); duplicate run same session (idempotency).

**Depends on:** TASK-003, TASK-007 (TASK-008 not required).

**Global constraints:** see `docs/CONSTRAINTS.md`.

**Status:** COMPLETE — merged to `main` ([PR #11](https://github.com/dubeyshantanu2/Aeolus/pull/11), commit `449bea3`), 2026-07-03. ADR approved, implemented, tested (see `reports/debug/TASK-009_debug-report.md`, `reports/qa/TASK-009_qa-report.md`), merged same day.
