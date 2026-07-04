# Architecture Decision Record — TASK-017

**Directive:** `directives/TASK-017_ml-model-trainer.md`
**Status:** APPROVED — planning merged via [PR #17](https://github.com/dubeyshantanu2/Aeolus/pull/17) (commit `909de7d`), 2026-07-04
**Date:** 2026-07-04

## Problem

Per config_type, fit scaler + Isolation Forest on the rolling `ml_feature_store` window, calibrate empirical flag/clear thresholds (never the contamination default), gate on warm-up criteria, and write a versioned registry row — batch, end-of-day, no online learning.

## Decision

`ModelTrainer.train_all()` loops the two config types independently; a failure in one never blocks the other (expiry data accrues ~5× slower and will be warming up for weeks while non-expiry goes live). Per config: `load_window` → drop-incomplete → warm-up gate → fit `StandardScaler`-equivalent μ/σ (σ floored per TASK-015's `SIGMA_FLOOR`) → fit `sklearn.ensemble.IsolationForest` → score the training set itself → `flag_threshold = quantile(scores, 1 - flag_pct)`, `clear_threshold = quantile(scores, 1 - clear_pct)` → serialize (joblib→base64) → insert `MLModelVersion` with `version = prev + 1`.

Thresholds are the hysteresis pair consumed by TASK-018: defaults `flag_pct = 0.05` (top 5%), `clear_pct = 0.10` — both config values, both recomputed every retrain so the cutoff tracks the current regime distribution (ML Spec §5.2). The spec's illustrative 0.74/0.66 are *examples of the resulting scores*, not constants.

Warm-up gate (both required): `sample_count >= warmup_min_samples_factor * n_features` (default 10×, ≈140 rows at 14 features — trivially met within one session at 5s cycles) AND `trading_day_count >= warmup_min_days` (default 15) — the day gate dominates by design, forcing regime diversity before go-live. Fail → no registry write; the config simply has no new version and TASK-018 keeps reporting `WARMING_UP`. Warm-up thresholds and all knobs live in `MLTuning` (pydantic-settings, mirroring `config/tuning.py` style but housed in `src/aeolus/ml/config.py` to preserve the engine/ML boundary). Defaults confirmed by OPEN_DECISIONS #7–#9 (2026-07-04): `window_days = 30`, `flag_pct = 0.05`, `clear_pct = 0.10`, daily EOD cadence.

IsolationForest params: `n_estimators=200`, `max_samples="auto"`, fixed `random_state` from config — retrains are reproducible from identical windows (determinism discipline extended to training). Rolling window: most recent `window_days=30` distinct sessions (OPEN_DECISIONS #7 — human chose 30 over the spec's 60 for faster drift adaptation); while history is shorter than that, the window is simply "all of it" (ML Spec §3.5 growing→rolling behavior falls out of `load_window`'s definition for free). Note the window (30 days) is only 2× the warm-up day gate (15) — the model goes live with half its steady-state regime diversity, which the daily retrain then tops up.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/ml/trainer.py` | `ModelTrainer`, fit/calibrate/serialize/version |
| `src/aeolus/ml/config.py` | `MLTuning` — window_days, flag_pct, clear_pct, warm-up factors, n_estimators, random_state, min_dwell (TASK-018), webhook env (TASK-020) |

## API Contracts

```python
class TrainResult(BaseModel):
    config_type: ConfigType
    outcome: Literal["TRAINED", "WARMING_UP", "FAILED"]
    version: int | None
    sample_count: int
    trading_day_count: int

class ModelTrainer:
    def __init__(self, store: FeatureStore, supabase_url: str, supabase_key: str,
                 *, tuning: MLTuning | None = None, client: Client | None = None): ...

    def train_all(self) -> list[TrainResult]:
        """Both configs, independent; exceptions per config are caught into
        outcome=FAILED. Called only from TASK-021's EOD hook, strictly after
        FeatureStore.sync_eod."""

    def train(self, config_type: ConfigType) -> TrainResult: ...
```

Registry keeps ALL prior versions (rollback + drift comparison, ML Spec Build Prompt 4); no pruning in v1.

## Performance / Failure Modes

30 days × ~4,500 cycles (5s interval) ≈ 135k rows × 14 features — IsolationForest with `max_samples="auto"` (256-sample trees) fits in seconds regardless of window size; runs post-close, latency irrelevant. Serialization round-trip is tested (`joblib` load of the base64 blob scores identically). Supabase failure mid-train → FAILED result, previous version stays active (scorer always reads latest *existing*). sklearn added to `pyproject.toml` (`scikit-learn>=1.5`, plus explicit `numpy`); `sklearn_version` recorded per row, checked at load (TASK-014 decision).

## Definition of Done

- [ ] Integration tests: synthetic window → TRAINED result; threshold == empirical quantile of training scores (hand-checked); round-trip blob scores match pre-serialization scores exactly
- [ ] Warm-up gate tests: enough rows but few days → WARMING_UP; enough days but few rows → WARMING_UP
- [ ] Two-config independence: expiry FAILED/warming while non-expiry TRAINED
- [ ] Version increments per config; prior rows untouched
- [ ] Constraint check: no hardcoded window/percentile literals outside `MLTuning`; no clock reads; never touches engine tables — reads `ml_feature_store` only
