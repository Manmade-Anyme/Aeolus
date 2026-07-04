# Architecture Decision Record — TASK-014

**Directive:** `directives/TASK-014_ml-schema-cleanup-denylist.md`
**Status:** DRAFT
**Date:** 2026-07-04 (revised same day after OPEN_DECISIONS #6 resolution)

## Problem

The ML anomaly module needs persistence that (a) survives independently of `signal_snapshots`, (b) can hold a serialized scikit-learn Isolation Forest + scaler + calibrated thresholds per version, and (c) coexists with a bounded database: at `CYCLE_INTERVAL_SECONDS = 5.0` the system writes ~4,500 cycles/session ≈ 20–25 MB/day across engine + ML tables (~5+ GB/year) on a Supabase instance shared with ARES. A real retention job is therefore in scope (OPEN_DECISIONS #6, resolved 2026-07-04).

## Decision

**Schema.** Three tables, `ml_feature_store` / `ml_model_registry` / `ml_anomaly_scores`, migrations `0009..0011`, following every TASK-001 convention: idempotent SQL applied by hand via the Supabase Dashboard SQL Editor (anon key cannot run DDL), `timestamptz` UTC, RLS disabled, `jsonb` for variable-shape payloads. Typed pydantic row models live in a NEW package `src/aeolus/ml/models.py` — deliberately **not** in `storage/models.py`, so no engine-side file ever references ML concepts.

**Model serialization.** `joblib.dump` → bytes → base64 → `text` column (`model_blob`). Registry rows store `sklearn_version`; the scorer refuses (treats as no-model/warm-up, logs loudly) a blob whose sklearn major.minor differs from the runtime's. Alternative considered: ONNX export — rejected, over-engineered for a single-consumer internal model.

**Retention job.** `RetentionJob` lives engine-side in `src/aeolus/jobs/retention.py` (sibling of `backfill.py`) — NOT in `aeolus.ml`, because the ML module is constrained to be strictly read-only against engine tables and a job that deletes `signal_snapshots` rows plainly is not. Policy (all config values on the job, no literals):

| Table | Action | Default |
|---|---|---|
| `signal_snapshots` | delete rows older than `retention_days` | 90 days |
| `ml_anomaly_scores` | delete rows older than `retention_days` | 90 days |
| `ml_model_registry` | keep newest `registry_keep_versions` per config_type | 30 |
| `ml_feature_store`, `state_transitions`, `daily_outlook`, `outcome_labels` | NEVER touched | — |

This supersedes ML Spec §6's blanket "all `ml_*` cleanup-protected" (human-approved): the training corpus is absolutely protected; the advisory log is age-trimmed; the registry is count-pruned. The future supervised phase keeps everything it needs from `ml_feature_store` (features, permanent) + `outcome_labels` (labels, permanent); `outcome_labels`' `ON DELETE SET NULL` FKs (migration 0005) absorb snapshot deletion by design — labels survive with a NULL source pointer, joinable to `ml_feature_store` via nothing (they pre-record their numbers) and to history via `ml_feature_store.source_snapshot_id` for snapshot-keyed labels written before deletion.

**Denylist guard.** `PROTECTED_TABLES: frozenset[str]` = the four never-touched tables, defined in `jobs/retention.py`; every delete/prune method asserts its target `not in PROTECTED_TABLES` before issuing SQL — protection is structural, not conventional. `src/aeolus/ml/models.py` separately exports `ML_PROTECTED_TABLES = {"ml_feature_store"}` documenting the ML module's own non-negotiable. Deletes are issued in batches (`.lt("ts", cutoff).limit(N)` loops) so a large first-run purge cannot time out PostgREST.

**Ordering.** The job only ever runs LAST in the EOD sequence `feature-store sync → retrain → cleanup` — wiring is TASK-021's scope (scheduler calls it after the ML hook returns; it also runs when ML is disabled, since a 90-day window can never race a same-day sync).

## Component Boundaries

| File | Responsibility |
|------|---|
| `supabase/migrations/0009_ml_feature_store.sql` | Feature store table + indexes + unique(source snapshot) |
| `supabase/migrations/0010_ml_model_registry.sql` | Registry table + indexes |
| `supabase/migrations/0011_ml_anomaly_scores.sql` | Scores table + indexes |
| `src/aeolus/ml/__init__.py` | Package marker (empty) |
| `src/aeolus/ml/models.py` | `MLFeatureRow`, `MLModelVersion`, `MLAnomalyScore`, `ML_PROTECTED_TABLES` |
| `src/aeolus/jobs/retention.py` | `RetentionJob`, `PROTECTED_TABLES`, batched deletes, registry pruning |

## API Contracts

```python
# src/aeolus/ml/models.py
ML_PROTECTED_TABLES: frozenset[str]  # {"ml_feature_store"} — the ML module's own absolute

class MLFeatureRow(BaseModel):
    TABLE: ClassVar[str] = "ml_feature_store"
    id: UUID
    ts: datetime
    session_date: date
    config_type: ConfigType            # reuse Literal from storage.models
    source_snapshot_id: UUID           # UNIQUE — idempotency anchor for TASK-016
    feature_set_version: int           # from TASK-015
    raw_values: dict[str, float]
    standardized_values: dict[str, float] | None

class MLModelVersion(BaseModel):
    TABLE: ClassVar[str] = "ml_model_registry"
    id: UUID
    config_type: ConfigType
    version: int                       # monotonic per config_type
    feature_set_version: int
    model_blob: str                    # base64(joblib bytes)
    sklearn_version: str
    scaler_mean: dict[str, float]
    scaler_std: dict[str, float]
    flag_threshold: float              # enter-anomalous (empirical top-5% percentile)
    clear_threshold: float             # exit-anomalous (top-10%, hysteresis pair)
    window_start: date
    window_end: date
    sample_count: int
    trading_day_count: int
    trained_at: datetime

class MLAnomalyScore(BaseModel):
    TABLE: ClassVar[str] = "ml_anomaly_scores"
    id: UUID
    ts: datetime
    session_date: date
    config_type: ConfigType
    source_snapshot_id: UUID
    score: float
    flagged: bool
    ml_status: Literal["ACTIVE", "WARMING_UP"]
    top_features: list[dict] | None    # [{name, z}] top 2–3, None when not flagged
    model_version_id: UUID | None

# src/aeolus/jobs/retention.py
PROTECTED_TABLES: frozenset[str]
# {"ml_feature_store", "state_transitions", "daily_outlook", "outcome_labels"}

class RetentionJob:
    def __init__(self, supabase_url: str, supabase_key: str, *,
                 retention_days: int = 90, registry_keep_versions: int = 30,
                 client: Client | None = None): ...

    def run(self, session_date: date) -> RetentionReport:
        """Trim signal_snapshots + ml_anomaly_scores older than retention_days;
        prune ml_model_registry beyond registry_keep_versions per config.
        Idempotent; batched; asserts every target not in PROTECTED_TABLES.
        Returns per-table deleted counts. Never raises upward (log + partial report)."""
```

SQL mirrors the models: `jsonb` for dict/list fields, native `config_type` enum reuse (0001), indexes `(ts)`, `(config_type)` on store/scores, `(config_type, version desc)` on registry, `UNIQUE(source_snapshot_id)` on the feature store. No FKs from `ml_*` to engine tables — `source_snapshot_id` is a plain UUID, so trimming `signal_snapshots` can never cascade into or block on `ml_*`.

## Performance / Failure Modes

Steady-state DB ≈ 1.5–2 GB (90 days × ~15 MB engine + ML dailies, minus permanent small tables). First production run may delete weeks of backlog → batched deletes with a row cap per batch. Partial failure (e.g. Supabase drop mid-purge) is safe: everything is re-derivable and the next run resumes by the same age predicate. Registry pruning keeps ≥ 1 version per config unconditionally (never deletes the active model even if config says 0).

## Definition of Done

- [ ] Integration-style tests against the real Supabase project (pattern of `tests/storage/test_supabase_integration.py`): insert/read round-trip per ML model, unique-violation on duplicate `source_snapshot_id`, registry version monotonicity
- [ ] Retention tests: seed in-window + out-of-window rows across all six affected/protected tables → run → out-of-window trimmed, in-window intact, protected counts UNCHANGED (Build Prompt 1's test), second run deletes zero
- [ ] Registry pruning keeps exactly `registry_keep_versions` newest per config; never below 1
- [ ] Pydantic contract tests (no DB) per model
- [ ] Constraint check: no engine file imports `aeolus.ml` (retention job is `jobs/`, imports nothing from `aeolus.ml`); migrations idempotent
