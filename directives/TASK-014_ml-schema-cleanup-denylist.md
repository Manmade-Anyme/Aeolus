# TASK-014 ML Supabase Schema + Retention/Cleanup Job

**Goal:** Create the three `ml_*` tables (feature store, model registry, anomaly scores) plus typed row models, AND the end-of-day retention job that keeps the shared Supabase instance bounded (~20–25 MB/day growth at 5s cycles) without ever touching protected tables.

**Acceptance Criteria:**
- [ ] Migrations `0009_ml_feature_store.sql`, `0010_ml_model_registry.sql`, `0011_ml_anomaly_scores.sql` (idempotent, hand-applied via Supabase SQL Editor — same convention as 0001–0008)
- [ ] Indexes: timestamp + config_type on feature store and scores; config_type + version on registry
- [ ] `ml_model_registry` can hold a serialized Isolation Forest + scaler params (μ/σ per feature) + flag/clear thresholds + window bounds + sample count + library version
- [ ] Pydantic models `MLFeatureRow`, `MLModelVersion`, `MLAnomalyScore` in `src/aeolus/ml/models.py` (TABLE classvar pattern from `storage/models.py`)
- [ ] `RetentionJob` in `src/aeolus/jobs/retention.py` (engine-side, NOT in `aeolus.ml`): trims `signal_snapshots` + `ml_anomaly_scores` rows older than `retention_days` (default 90); prunes `ml_model_registry` to last `registry_keep_versions` (default 30) per config; all values config, no hardcoded literals
- [ ] Denylist guard: job structurally refuses to touch `ml_feature_store`, `state_transitions`, `daily_outlook`, `outcome_labels` — protected set defined as a constant the job asserts against
- [ ] Build Prompt 1 test: run cleanup against real data, assert protected-table row counts unchanged and only out-of-window rows removed
- [ ] Idempotent: re-running cleanup for the same day deletes nothing further

**Inputs:** ML Spec §6 (as amended by OPEN_DECISIONS #6 resolution — blanket `ml_*` protection superseded); Build Prompt 1; `docs/DATA_MODEL.md` access pattern (supabase-py + anon key, RLS disabled, DDL by hand).

**Output:** `supabase/migrations/0009..0011_*.sql`, `src/aeolus/ml/models.py`, `src/aeolus/jobs/retention.py`, docs updates.

**Edge Cases:** cleanup must run LAST in the EOD sequence (`sync → retrain → cleanup`, wired by TASK-021) — running it standalone before a feature-store sync would still be safe only for data older than 90 days, which sync lag can never approach; delete batching so a large first-run purge doesn't time out PostgREST; `outcome_labels` FKs going NULL on snapshot deletion is expected (`ON DELETE SET NULL`, migration 0005 — labels survive, features live on in `ml_feature_store`).

**Depends on:** TASK-001 (schema conventions).

**Global constraints:** see `docs/CONSTRAINTS.md` — including the ML overlay constraints (advisory-only; retention policy per OPEN_DECISIONS #6; IF decides / z-score explains; deterministic explanations).

**Status:** DRAFT — OPEN_DECISIONS #6 RESOLVED 2026-07-04 (retention job in scope, policy 90d / keep-30-versions)
