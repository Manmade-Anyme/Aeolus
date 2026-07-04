# Debug Report — TASK-017

**Date:** 2026-07-04
**Verdict:** ✅ CLEAN (blocked only on the carried-over migration-apply step)

## What was run
- `pytest tests/ml/test_trainer_integration.py -q` — 5 live-Supabase tests; **errored**, same `PGRST205` cause as every other TASK-014-onward live test (`ml_feature_store` not yet in the schema cache — migrations 0009-0011 still not hand-applied).
- Standalone sanity script (not committed, scratch) exercising `ModelTrainer.train()` end-to-end against a hand-rolled in-memory fake Supabase client (duck-typed `.table().select().eq().order().limit().insert().execute()`) — this is the only way to actually execute the fit/threshold/serialization code path before the live migrations land. Caught and fixed a real bug: **`joblib.dumps`/`joblib.loads` don't exist** (joblib only has `dump`/`load`, file-object-based, unlike `pickle.dumps`/`loads`) — original code raised `AttributeError` on the very first `train()` call. Fixed via `io.BytesIO()` buffers in both `trainer.py` and the test's round-trip verification. Re-ran the sanity script after the fix: `train()` → `TRAINED`, second `train()` → version 2, and independently recomputed `flag_threshold` from the deserialized blob matched the stored value exactly (`0.4867360459358814` both sides).
- `ruff check` + `mypy` (run together across `tests/ml/` + `src/aeolus/ml/` — running mypy on a single new test file in isolation spuriously reports `import-not-found` for sibling `aeolus.ml.*` modules; this is a path-resolution quirk of single-file invocation, not a real error, confirmed by running the whole package together) — clean.
- `pytest -q` (full suite) — 251 passed, only the same pre-existing unrelated ingestion failure plus all migration-pending errors/failures, no new regressions.

## Observed behavior
Sanity script output:
```
config_type='NON_EXPIRY' outcome='TRAINED' version=1 sample_count=192 trading_day_count=16
config_type='NON_EXPIRY' outcome='TRAINED' version=2 sample_count=192 trading_day_count=16
registry rows: 2
flag_threshold stored: 0.4867360459358814 expected: 0.4867360459358814
OK
```

## Issues
| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| 1 | Bug (caught, fixed pre-commit) | `joblib.dumps`/`joblib.loads` don't exist — `AttributeError` on first real `train()` call | `src/aeolus/ml/trainer.py` (model serialization) | Fixed via `io.BytesIO` + `joblib.dump`/`joblib.load` |
| 2 | Blocker (human action, carried over) | Migrations 0009–0011 still not applied to the live Supabase project | `supabase/migrations/0009_ml_feature_store.sql` etc. | Open |

## Constraint audit
- [x] No per-signal veto — n/a, batch training/calibration, no scoring/gating
- [x] No clock-time branching — grep confirms the only `datetime.now()` call in `trainer.py` timestamps `trained_at`, never branches logic on wall-clock time; `session_date`/window selection flows entirely from `FeatureStore.load_window`'s parameters
- [x] Reason strings deterministic — n/a
- [x] Polarity — n/a
- [x] `system_status` never feeds `market_state` — n/a
- [x] No hardcoded window/percentile literals outside `MLTuning` — grep confirms `window_days`/`flag_pct`/`clear_pct`/`warmup_min_samples_factor`/`warmup_min_days`/`n_estimators`/`random_state` are all read from `self._tuning`, never inlined
- [x] Never touches engine tables — `trainer.py` only ever calls `self._store.load_window` (which itself only reads `ml_feature_store`) and writes to `ml_model_registry`; no reference to `signal_snapshots` anywhere in the file
