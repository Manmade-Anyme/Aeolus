# Architecture Decision Record — TASK-021

**Directive:** `directives/TASK-021_ml-orchestration-hooks.md`
**Status:** APPROVED — planning merged via [PR #17](https://github.com/dubeyshantanu2/Aeolus/pull/17) (commit `909de7d`), 2026-07-04
**Date:** 2026-07-04

## Problem

Wire feature append (016), live scoring (018), explanation (019) and posting (020) into the scheduler's cycle, and `sync → retrain → cleanup` into its end-of-day path — such that the engine runs identically whether the ML module is present, absent, or broken.

## Decision

A single `MLHooks` facade owns all ML collaborators and exposes exactly two methods, both total (never raise): `on_cycle(signal_snapshot)` and `on_end_of_day(session_date)`. Internally every step is individually try/except-wrapped with `logger.exception` — an append failure must not stop scoring, a scoring failure must not stop the append, and nothing propagates to the caller. This matches the scheduler's existing containment idiom (`_run_live_loop`'s per-cycle catch, `_run_backfill`'s swallow).

Scheduler wiring is the minimal diff the DI pattern already invites: new keyword `ml_hooks: MLHooks | None = None` on `Scheduler.__init__`. Default is None — **the ML module is opt-in at composition root**, so `Scheduler()` as used today is byte-for-byte unaffected and no scheduler import of `aeolus.ml` executes unless the entrypoint constructs the hooks. (Type-only reference via `TYPE_CHECKING` or a Protocol keeps even the import optional; use a `MLHooksProtocol` structural type in scheduler.py so scheduler never imports `aeolus.ml` at runtime at all.) Call sites: in `_run_live_loop`, immediately after `self._engine.run_cycle(...)` returns, `self._ml_hooks.on_cycle(signal_snapshot)`; in `run()`, after `self._run_backfill(today)`, `self._ml_hooks.on_end_of_day(today)`.

EOD order inside `on_end_of_day` is sequential and explicit: `store.sync_eod(session_date)` then `trainer.train_all()` — sync's success is a precondition checked before retrain (sync raising internally → retrain still runs on whatever the store holds, which is safe per ML Spec §3.2, but the failure is logged at error level). Cleanup (TASK-014's `RetentionJob`, OPEN_DECISIONS #6) is deliberately NOT inside `MLHooks`: the scheduler itself runs `retention_job.run(today)` after the ML hook returns, via its own injectable `retention_job: RetentionJob | None` collaborator — so the mandated `sync → retrain → cleanup` order holds by call sequence, and retention still runs when ML is disabled (`ml_hooks=None`). Retrain cadence: daily EOD (OPEN_DECISIONS #9). `on_cycle` also drives TASK-020's go-live/warm-up notices using trainer/registry state observed at session start.

Alternative considered — engine-internal hook (inside `Engine.run_cycle`): rejected; the engine must stay ML-ignorant, and the scheduler is the component whose job is sequencing.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/ml/hooks.py` | `MLHooks` facade, failure isolation, EOD ordering, event → explain → post plumbing |
| `src/aeolus/scheduler/scheduler.py` | +`ml_hooks` kwarg, +`retention_job` kwarg, three guarded call sites (only engine-side diff of TASK-014..021 besides `jobs/retention.py`) |

## API Contracts

```python
class MLHooks:
    def __init__(self, supabase_url: str, supabase_key: str,
                 *, tuning: MLTuning | None = None,
                 store: FeatureStore | None = None, scorer: LiveScorer | None = None,
                 trainer: ModelTrainer | None = None,
                 dispatcher: MLDiscordDispatcher | None = None): ...

    def start_session(self, session_date: date) -> None:
        """Reset scorer state; detect go-live transitions to announce."""

    def on_cycle(self, snapshot: SignalSnapshot) -> None:
        """append -> score -> (on event) explain -> post. NEVER raises."""

    def on_end_of_day(self, session_date: date) -> None:
        """sync_eod -> train_all, strict order. NEVER raises."""
```

## Performance / Failure Modes

Adds ≤ ~2 Supabase writes + one in-memory score per cycle — no impact on the 60s cycle budget. Kill-switch: omit `ml_hooks` at the entrypoint. Every directive-listed failure mode maps to a test: ML raising every cycle → session completes; retrain fails → previous model version remains latest; crash between sync and retrain → next `sync_eod` idempotently heals.

## Definition of Done

- [ ] Scheduler tests (existing injectable style): ml_hooks=None → behavior identical to today (no new calls); hooks raising internally → live loop and teardown unaffected
- [ ] EOD ordering test: sync_eod observed complete before train_all begins; retention_job.run begins only after the ML hook returns (observable via real store/registry state, not call-order mocks); retention runs with ml_hooks=None
- [ ] on_cycle full path integration test with toy fitted model: flag → one advisory posted via HTTP-boundary mock
- [ ] `grep` gate: no runtime import of `aeolus.ml` in `src/aeolus/{engine,signals,ingestion,outlook,explain,output,jobs}/`
- [ ] Constraint check: scheduler remains the only clock-aware component; ML never writes engine tables; advisory-only preserved end-to-end
