# QA Report — TASK-021

**Date:** 2026-07-04
**Verdict:** ✅ PASS

## Test summary
| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/ml/test_hooks.py` (fake collaborators, no DB) | 18 | 18 | 0 | per-step failure isolation (append/score/explain/post/sync/train/reset/announce), EOD ordering, go-live/warm-up detection, both configs independent |
| `tests/ml/test_hooks_integration.py` (live Supabase + toy fitted model, HTTP-mocked dispatcher) | 1 | 1 | 0 | full `on_cycle` chain: real append, real score+flag, real registry-version resolution, exactly one advisory posted with correct content |
| `tests/scheduler/` (existing + new) | 15 | 15 | 0 | `ml_hooks=None` parity (11 pre-existing tests all still pass unmodified + 1 new explicit test), 3-call-site wiring, hook-raises-everywhere containment, retention-after-ml-hooks ordering |
| `tests/test_ml_import_boundary.py` (AST scan, no DB) | 2 | 2 | 0 | zero `aeolus.ml` imports in `engine/signals/ingestion/outlook/explain/output/jobs/`; scheduler.py specifically |
| Full repo regression | 323 | 323 | 0 | 1 pre-existing, unrelated live-Dhan-API failure excluded from this count |

## Scenarios covered
- **`on_cycle` full path:** append → score → (on transition) explain → post, in that order; verified both that a non-event cycle posts nothing and that an entering/clearing cycle posts exactly the right message type with the registry-resolved `version`/thresholds baked into TASK-019's templated reason string (unit-level with fakes, and live end-to-end with a real fitted model).
- **Per-step failure isolation:** append raising doesn't block scoring; scoring raising doesn't propagate; explain/post raising doesn't propagate; none of the four `on_cycle` sub-steps can take down the others or the caller.
- **EOD ordering:** `sync_eod` observed to complete before `train_all` begins (shared order-log, unit level) — matches the ADR's "sync is retrain's only data source" precondition. Sync or train individually raising doesn't block the other from attempting to run, nor does it propagate.
- **Go-live/warm-up detection:** no registry row + zero collected days → no post; no registry row + N collected days → `post_warmup_progress(config_type, day=N, target=warmup_min_days)`; registry version == 1 → `post_golive(config_type, model_version=1)`; registry version > 1 → no post (already live, nothing to announce); the two configs (`EXPIRY`/`NON_EXPIRY`) are verified independent of each other in every one of these branches.
- **Scheduler wiring:** `ml_hooks=None` (the default) leaves every pre-existing TASK-013 scheduler test passing byte-for-byte unmodified, plus a new explicit test confirming retention still runs; injecting a real (fake) `MLHooks` double confirms it's called exactly at `start_session` (once, before the live loop), `on_cycle` (once per cycle, with the just-scored `SignalSnapshot`), and `on_end_of_day` (once, after backfill); a hook that raises on all three methods still lets `engine.end_session()`, the backfill job, and the retention job all complete normally.
- **Retention wiring:** `RetentionJob.run` fires unconditionally (with or without `ml_hooks`) and strictly after `ml_hooks.on_end_of_day` returns, proven via a shared order-log at the scheduler level (complementing the live, real-state proof of the *store/registry* half of the ordering requirement inside `MLHooks.on_end_of_day` itself).
- **Import boundary:** an AST-based scan (not a fragile string grep) confirms no `import aeolus.ml` / `from aeolus.ml import ...` anywhere in the 7 restricted packages, and specifically in `scheduler.py`, which references `MLHooks` only via a locally-defined structural `Protocol`.

## Edge cases exercised
- **ML raises on every cycle:** `test_ml_hooks_exceptions_at_every_call_site_never_crash_the_scheduler` — the directive's own edge case, verified at the scheduler level (not just inside `MLHooks`, which is trivially safe by its own contract) so the guarantee holds even against a non-conforming injected hook.
- **Retrain fails, previous model stays active:** covered structurally — `on_end_of_day`'s `train_all` failure is caught and logged, and nothing in the live path (`on_cycle`) depends on retrain having just succeeded; the previously-cached/registry model keeps serving `score_cycle` regardless (this half is already covered by TASK-018's own test suite; TASK-021 only needed to confirm the EOD wrapper doesn't propagate, which it doesn't).
- **Process killed between sync and retrain, next EOD heals:** not separately re-tested here — this is `FeatureStore.sync_eod`'s own idempotency guarantee (TASK-016, already tested: a second `sync_eod` call is a no-op) plus `MLHooks.on_end_of_day` simply calling `sync_eod` then `train_all` in sequence every time; there is no new state introduced by this task that could make a crash-between-steps unrecoverable.
- **Missing registry row at explain time** (model deleted/pruned between `score_cycle` writing the event and `_explain_and_post` reading the registry): `test_on_cycle_missing_registry_row_skips_post_without_raising` covers this directly — logs a warning, skips the post, does not raise.

## Gaps / follow-ups
- `MLHooks`'s go-live detection relies on `version == 1` uniquely meaning "just went live" (version strictly increments, daily retrain). If retrain stalls at v1 for multiple days (a `FAILED`/`WARMING_UP`-adjacent edge case not itself in this task's scope), the go-live notice could in principle re-fire on a later session's `start_session`. This is a known, low-probability, low-severity limitation (documented in `reports/debug/TASK-021_debug-report.md` and the ADR-deviation note in `src/aeolus/ml/hooks.py`'s docstring) rather than a fix, since introducing a persisted "already announced" marker was explicitly weighed against and rejected in TASK-020's own ADR for the analogous warm-up-line problem ("schema surface" tradeoff) — the same judgment applies here.
- No test exercises a real live-Supabase EOD ordering proof of `sync_eod` fully populating `ml_feature_store` before `train_all` reads a non-empty `load_window` (the unit-level order-log test proves call sequence; TASK-016/017's own live suites already separately prove `sync_eod`'s and `load_window`'s individual correctness against real data) — combining both into one live test was judged unnecessary duplication of already-covered ground.
