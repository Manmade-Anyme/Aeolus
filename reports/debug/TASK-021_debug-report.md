# Debug Report — TASK-021

**Date:** 2026-07-04
**Verdict:** ✅ CLEAN

## What was run
- `pytest tests/ml/test_hooks.py -q` — 18 no-DB tests (fake collaborators) covering `MLHooks`'s failure isolation, EOD ordering, and go-live/warm-up detection logic.
- `pytest tests/ml/test_hooks_integration.py -q` — 1 live-Supabase test: real `FeatureStore`+`LiveScorer`+fitted `IsolationForest`, HTTP-mocked `MLDiscordDispatcher`, full `on_cycle` append→score→explain→post chain.
- `pytest tests/scheduler/ -q` — 15 tests (11 existing + 4 new: `ml_hooks=None` still runs retention, hooks wired at all 3 call sites, hooks raising everywhere never crashes the scheduler, retention runs strictly after `ml_hooks.on_end_of_day`).
- `pytest tests/test_ml_import_boundary.py -q` — 2 tests: AST-based grep-equivalent scan confirming zero `aeolus.ml` imports anywhere under `engine/signals/ingestion/outlook/explain/output/jobs/`, and specifically in `scheduler.py`.
- `pytest tests/ -q` (full repo) — 323 passed, 1 failed (pre-existing, unrelated live-Dhan-API failure in `test_ingestion_service_end_to_end`).
- `ruff check` + `mypy` on `src/aeolus/ml/hooks.py`, `src/aeolus/scheduler/scheduler.py`, and all new/modified test files — clean.

## Observed behavior
All new tests pass. One real bug caught and fixed before committing: the first draft of `tests/ml/test_hooks.py` had an unused `pytest` import (ruff `F401`), removed once no test in that file ended up needing a `pytest` fixture/decorator directly (all failure-injection is done via plain dataclass fields, not `monkeypatch` or `pytest.raises`).

The live integration test (`test_hooks_integration.py`) reused the toy-fitted-`IsolationForest` technique from TASK-018's `test_scorer_integration.py` verbatim (different `random_state`/session date to avoid any collision) and passed on the first correct run against the real Supabase project, confirming the full chain — `FeatureStore.append` (real row landed in `ml_feature_store`), `LiveScorer.score_cycle` (real flagged row in `ml_anomaly_scores`), `MLHooks._explain_and_post`'s registry lookup (resolved the inserted row's `version=4` correctly into the reason string), and `MLDiscordDispatcher.post_anomaly` (exactly one HTTP POST, correct title/footer) — are wired together correctly end to end, not just correct in isolation.

## Issues
| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| 1 | Lint (caught pre-commit) | Unused `pytest` import | `tests/ml/test_hooks.py` | Fixed |
| — | — | No production-code issues found | — | — |

## Constraint audit
- [x] No per-signal veto present — n/a; `MLHooks` never gates the engine, it only appends/scores/explains/posts *after* the engine has already produced its own `market_state` decision independently
- [x] No clock-time branching in signal logic — grep confirms `hooks.py` has zero `datetime.now`/`date.today`/wall-clock reads; `session_date` is always caller-supplied (from the scheduler, which remains the only clock-aware component); `test_scheduler_module_does_not_import_aeolus_ml` + the restricted-package scan both pass, confirming the scheduler stays the sole clock-reading module
- [x] Reason strings deterministic — n/a here; `MLHooks` only threads registry-resolved `version`/`flag_threshold`/`clear_threshold` into TASK-019's already-deterministic templates, never re-words them
- [x] Polarity check: GO favors option buying — n/a, advisory ML overlay, never touches `market_state`
- [x] `system_status` never feeds `market_state` — n/a; `MLHooks` only reads `SignalSnapshot.system_status` indirectly (via `FeatureStore.append`'s existing STALE/DISCONNECTED refusal), never writes to any engine table
- [x] Engine runs identically with `ml_hooks=None` — `test_ml_hooks_none_by_default_but_retention_still_runs` confirms `scheduler.run()` completes and retention still fires with zero `ml_hooks` wiring; every pre-existing TASK-013 scheduler test (which never passes `ml_hooks`) continues to pass unmodified
- [x] No engine/scheduler file imports `aeolus.ml` at runtime except the optional wiring point — `tests/test_ml_import_boundary.py`'s AST scan is the actual enforcement mechanism (stronger than a one-off `grep` since it parses real `import`/`from...import` nodes, ignoring strings/comments); `scheduler.py` only references `MLHooksProtocol`, a structural `typing.Protocol` defined locally with zero `aeolus.ml` import
- [x] Every hook call wrapped, ML exception never breaks the engine loop or session teardown — enforced at *two* layers: internally (every step inside `MLHooks`'s three public methods is individually try/except'd) and at the scheduler's three call sites (belt-and-suspenders, since the scheduler's own contract — "runs identically with `ml_hooks=None` or broken" — must hold even against a caller-supplied hook implementation that doesn't honor `MLHooks`'s own contract); `test_ml_hooks_exceptions_at_every_call_site_never_crash_the_scheduler` injects a hook that raises on all three methods and confirms `engine.ended`, `backfill_job.calls`, and `retention_job.calls` all still fire normally
- [x] EOD order (`sync -> retrain -> cleanup`) — `test_on_end_of_day_syncs_before_training` proves sync-before-train via a shared order-log (unit level); `test_retention_runs_after_ml_hooks_end_of_day_returns` proves retention-after-ml-hooks via the same shared-order-log technique at the scheduler level; the live integration test additionally proves the append/score/explain/post sub-chain works against real Supabase state, not mocks
