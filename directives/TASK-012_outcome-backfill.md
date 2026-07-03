# TASK-012 Outcome-Label Backfill Job

**Goal:** After-the-fact enrichment job: join forward realized outcomes (+15/+30/+60 min straddle price change, realized move, direction) onto `signal_snapshots`/`state_transitions`, and backfill realized archetype onto `daily_outlook`.

**Acceptance Criteria:**
- [x] Writes `outcome_labels` rows keyed to snapshots/transitions
- [x] Updates `daily_outlook.realized_archetype` for the session
- [x] Runs end-of-day or delayed — NEVER live/synchronously with the scoring loop (labels don't exist at signal-time)
- [x] Idempotent: re-running for a session doesn't duplicate labels

**Inputs:** Stored session data (TASK-001); Spec §9/§11, Build Prompt 12.

**Output:** `src/aeolus/jobs/`.

**Edge Cases:** snapshots within 60 min of close (truncated forward window — policy defined in ADR); halted/shortened sessions; missing forward data for a timestamp.

**Depends on:** TASK-001, TASK-008 (needs rows to enrich), TASK-009.

**Global constraints:** see `docs/CONSTRAINTS.md`.

**Status:** COMPLETE — ADR approved, implemented, tested (see `reports/debug/TASK-012_debug-report.md`, `reports/qa/TASK-012_qa-report.md`), 2026-07-03.
