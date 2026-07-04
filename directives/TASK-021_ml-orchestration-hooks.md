# TASK-021 ML Orchestration Hooks

**Goal:** Wire the ML module into the existing scheduler as a strict, failure-isolated consumer: live hook after each engine cycle, end-of-day hook enforcing `feature-store sync → retrain → (future cleanup)`.

**Acceptance Criteria:**
- [ ] `MLHooks` facade: `on_cycle(signal_snapshot)` (append + score + explain + post on flag) and `on_end_of_day(session_date)` (sync → retrain, strict order)
- [ ] Scheduler integration: optional injectable `ml_hooks` on `Scheduler` (same DI pattern as its other collaborators); called after `engine.run_cycle` in the live loop and after `_run_backfill` in `run()`
- [ ] Retention wiring: scheduler runs `RetentionJob` (TASK-014) LAST — after `ml_hooks.on_end_of_day` returns — completing the mandated `sync → retrain → cleanup` order; retention runs even when `ml_hooks=None`
- [ ] Every hook call wrapped — any ML exception degrades to "no advisory" + log, NEVER breaks the engine loop or session teardown
- [ ] Engine runs identically with `ml_hooks=None` — the ML module is a consumer of the engine, never a dependency of it; no engine/scheduler file imports `aeolus.ml` at module level except the optional wiring point
- [ ] EOD order test: sync completes before retrain begins; cleanup begins only after both

**Inputs:** ML Spec §7; Build Prompt 8; `src/aeolus/scheduler/scheduler.py` (`run()`, `_run_live_loop`).

**Output:** `src/aeolus/ml/hooks.py`, minimal `scheduler.py` wiring diff.

**Edge Cases:** ML raises on every cycle (engine must complete session normally); retrain fails (yesterday's model stays active); process killed between sync and retrain (next EOD heals — sync is idempotent).

**Depends on:** TASK-014 (RetentionJob), TASK-016, TASK-017, TASK-018, TASK-019, TASK-020. Retrain cadence: daily EOD (OPEN_DECISIONS #9, resolved 2026-07-04).

**Global constraints:** see `docs/CONSTRAINTS.md` (incl. ML overlay constraints). Scheduler stays the only clock-aware component — hooks fire on scheduler events, never on wall-clock reads of their own.

**Status:** APPROVED — planning merged via [PR #17](https://github.com/dubeyshantanu2/Aeolus/pull/17) (commit `909de7d`), 2026-07-04
