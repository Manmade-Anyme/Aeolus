# TASK-013 Orchestration / Scheduler

**Goal:** Wire TASK-002..011 into the continuous live loop (9:15–15:30 IST), plus single pre-open trigger for TASK-009 and single post-close trigger for TASK-012.

**Acceptance Criteria:**
- [ ] Loop runs across full NSE session; schedule-gating ("is the market open") lives HERE and only here
- [ ] No module inside the loop branches its own logic on current time — scheduler owns *when*, never *how to interpret*
- [ ] Pre-open: TASK-009 fires exactly once per session
- [ ] Post-close: TASK-012 fires once per session
- [ ] NSE trading calendar aware (holidays, shortened sessions)
- [ ] Graceful shutdown/restart mid-session without corrupting state (debounce counters, CVD continuity via TASK-002)

**Inputs:** All prior modules; Spec §7/§13, Build Prompt 13.

**Output:** `src/aeolus/scheduler/`; single entrypoint to run AEOLUS.

**Edge Cases:** process restart mid-session; clock skew; muhurat/special sessions; deploy during market hours.

**Depends on:** TASK-002..012 (build LAST).

**Global constraints:** see `docs/CONSTRAINTS.md` — constraint #2's only permitted clock-awareness lives here, and only as "is market open."

**Status:** DRAFT
