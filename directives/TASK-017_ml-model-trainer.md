# TASK-017 ML Model Trainer

**Goal:** End-of-day batch trainer: per config_type, fit scaler + Isolation Forest on the rolling window from `ml_feature_store`, calibrate empirical flag/clear thresholds, write a versioned row to `ml_model_registry`.

**Acceptance Criteria:**
- [ ] Two independent models — `EXPIRY` and `NON_EXPIRY` — never one model across both
- [ ] Fit sequence per config: load window → fit scaler (μ/σ) → standardize → fit Isolation Forest → score training set → flag threshold = configured empirical percentile of training scores (default top 5%), clear threshold = separate lower percentile (hysteresis pair)
- [ ] Warm-up gating (ML Spec §5.4): window must be statistically fittable (≥ 10 × n_features samples) AND regime-representative (≥ N distinct trading days, default 15) — else write no usable model, mark config `WARMING_UP`
- [ ] Versioned registry write: serialized model + scaler + thresholds + window bounds + sample count + sklearn version + trained-at; prior versions retained for rollback/drift comparison
- [ ] Rolling window length, percentiles, warm-up thresholds are config values — no hardcoded literals

**Inputs:** ML Spec §3.3, §3.5, §5.2, §5.4, §10; Build Prompt 4.

**Output:** `src/aeolus/ml/trainer.py`, `src/aeolus/ml/config.py` (ML tuning values, pydantic-settings pattern).

**Edge Cases:** window with rows from only a few distinct days (gate on days, not just row count); expiry config accumulating far slower (~1 day/week — will warm up weeks after non-expiry); feature rows with missing values (dropped from training window); serialization round-trip fidelity.

**Depends on:** TASK-014, TASK-015, TASK-016.

**Global constraints:** see `docs/CONSTRAINTS.md` (incl. ML overlay constraints). Batch retrain only — no online learning in v1.

**Status:** DRAFT — Open decisions RESOLVED 2026-07-04: #7 window = **30 trading days** (not spec's 60), #8 flag = top 5% / clear = top 10%, #9 daily EOD retrain.
