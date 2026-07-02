# TASK-009 Pre-Market Outlook Generator

**Goal:** Single-run pre-open job producing the Spec §5.1 probabilistic day-archetype forecast, written to `daily_outlook`, led by the prior-day trend-exhaustion check.

**Acceptance Criteria:**
- [ ] ⭐ HEADLINE DRIVER: if yesterday's profile (TASK-007) resolved as clean/elongated trend day, this is the LEAD LINE of the output, ahead of all other inputs, raising the prior for digestion/consolidation (archetypes 2/3). This is the pattern the project exists to catch (Spec §1).
- [ ] Inputs weighed: GIFT Nifty gap, yesterday's profile shape, ATM straddle premium level vs 10–20 session history (from TASK-003's separate pre-market function), IV percentile/VIX heading in, OI/max-pain carryover, futures price context, DTE
- [ ] Output = archetype probability distribution + primary/secondary call + confidence — forecast-prior vocabulary, NEVER NO-GO/PREPARE/GO
- [ ] Writes exactly one `daily_outlook` row per session; trend-exhaustion flag stored as its own field
- [ ] Realized-archetype backfill NOT here (that's TASK-012)

**Inputs:** TASK-002 (GIFT Nifty, futures), TASK-003 (straddle-history fn), TASK-007 (profile flag, DTE); Spec §5.1, Build Prompt 9.

**Output:** `src/aeolus/outlook/`.

**Edge Cases:** GIFT Nifty unavailable pre-open; first session (no prior-day data); duplicate run same session (idempotency).

**Depends on:** TASK-003, TASK-007 (TASK-008 not required).

**Global constraints:** see `docs/CONSTRAINTS.md`.

**Status:** DRAFT
