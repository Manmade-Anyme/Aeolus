# AEOLUS Anomaly ML — Build Prompts
**Companion to AEOLUS_ML_ANOMALY_SPEC.md**

## How to use this

Feed `AEOLUS_ML_ANOMALY_SPEC.md` as shared context first, alongside the original
`AEOLUS_SYSTEM_SPEC.md` (the ML module reads the engine's `signal_snapshots` and
reuses its config/DTE flag, so the Architect needs both). Prompts are numbered in
dependency order — don't build the scorer (5) before the trainer (4), or the
trainer before the feature store (1–3).

**Four global constraints inherited from the spec, restated so they survive being
pasted individually:**
- **Advisory only.** This module never writes to engine tables, never changes
  NO-GO/PREPARE/GO, never gates a trade. Read-only against `signal_snapshots`.
- **Cleanup-protected persistence.** All `ml_*` tables must be excluded from the
  end-of-day cleanup, and the end-of-day order is strictly
  `feature-store sync → retrain → cleanup`.
- **Deterministic explanations.** Top-feature attribution is templated from
  numbers (z-scores), never LLM-narrated.
- **Isolation Forest decides; Mahalanobis/z-score only explains.** Do not let the
  per-feature attribution become the flag decision.

---

### 1. ML Supabase Schema + Cleanup Denylist Change

**Objective:** Create `ml_feature_store`, `ml_model_registry`,
`ml_anomaly_scores` (Spec Section 6), and modify the existing end-of-day cleanup
job to exclude all three.

**Deliverables:** migrations for the three tables (indexes on timestamp +
config_type + model version); serialized-model column in `ml_model_registry`
able to hold the fitted Isolation Forest + scaler params + threshold; a config
change to the cleanup job adding the `ml_*` tables to its protected set.

**Constraints:** the cleanup change is part of *this* task — the module is unsafe
to run until cleanup is proven to skip `ml_*`. Include a test that runs cleanup
and asserts `ml_*` row counts are unchanged.

---

### 2. Feature Extractor

**Objective:** Transform a `signal_snapshots` row into the standardized feature
vector (Spec Section 4 feature table + Section 5.1).

**Deliverables:** a pure function `snapshot → raw_feature_vector`, plus a
`standardize(raw_vector, scaler)` that applies a *stored* scaler (μ/σ per
feature). Scaler fitting lives in the trainer (Module 4), not here — this module
only applies a scaler it is given.

**Constraints:** exclude rows where `system_status` is `STALE`/`DISCONNECTED`.
Never re-fit the scaler on a single live vector. Feature order must be fixed and
versioned so a stored model always receives features in the order it trained on.

---

### 3. Feature-Store Sync Job

**Objective:** Persist every scored cycle's vector into `ml_feature_store` so the
training corpus survives the cleanup that trims `signal_snapshots`.

**Deliverables:** an idempotent sync that copies any not-yet-persisted vectors
(live path appends per cycle; end-of-day sync is a backstop catch-up). Stores
both raw and standardized values plus the source snapshot id and config_type.

**Constraints:** must complete **before** cleanup in the end-of-day sequence.
Idempotent — re-running must not duplicate rows.

---

### 4. Model Trainer

**Objective:** Fit, per config_type, an Isolation Forest + the scaler (μ/σ) +
the empirical flag threshold, on the rolling window from `ml_feature_store`
(Spec Sections 3.5, 5.2, 5.4).

**Deliverables:**
- Per-config fit: standardize on the window, fit Isolation Forest, compute the
  flag threshold as the configured empirical percentile of training scores
  (default top 5%).
- Warm-up gating (Section 5.4): if a config's window fails the fittable /
  regime-representative thresholds, write no usable model — mark it warming up.
- Versioned write to `ml_model_registry` (serialized model, scaler, threshold,
  window bounds, sample count, timestamp). Keep prior versions for rollback and
  drift comparison.

**Constraints:** two independent models (expiry / non-expiry) — never one model
across both. Rolling window length, flag percentile, and cadence are config
values (Spec Section 10 defaults), not hardcoded literals.

---

### 5. Live Scorer

**Objective:** Each cycle, load the latest model for the current config, score
the current vector, and record it (Spec Section 7 live path).

**Deliverables:** load latest `ml_model_registry` version for config_type →
standardize with its stored scaler → Isolation Forest score → compare to stored
threshold → write `ml_anomaly_scores` (score, flag, warm-up status, model
version). Also triggers Module 6 when flagged and Module 3's append.

**Constraints:** if no active model or warming up → record score, emit
`WARMING_UP` and the daily warm-up progress line (once per day, not per cycle),
raise no flag. Skip scoring entirely on `STALE`/`DISCONNECTED` cycles.
**Posting must use both debounce and hysteresis (Spec Section 7.1):** post only
on the transition into anomalous (debounce), and use separate upper-enter /
lower-clear thresholds so a boundary-hovering score does not flap (hysteresis).
A normal day must produce zero posts; an anomalous stretch produces one advisory
on entry, at most one clear on exit.

---

### 6. Explainability / Top-Feature Attribution

**Objective:** For a flagged vector, rank features by standardized deviation
(|z_i|, or Mahalanobis per-term contribution) and produce a deterministic reason
string naming the top 2–3 driving dimensions with their z-scores (Spec 5.3).

**Constraints:** this is *explanation only* — it must never influence whether the
vector was flagged (that was Module 5's Isolation Forest decision). Deterministic:
identical vector + model → identical string.

---

### 7. Discord Anomaly Output Formatter

**Objective:** Format and post the message types (Spec Section 8) — anomaly
advisory (on entry), optional anomaly-cleared (on exit), daily warm-up progress
line, and warm-up go-live notice — visually distinct from engine messages.

**Deliverables:** advisory message with flag, score, top dimensions + z-scores,
model version, and an explicit *"advisory only — does not change engine state"*
footer. Daily warm-up progress line (one per day max) while a config is still
warming up. Warm-up go-live notice posted once when a config's model first goes
active.

**Constraints:** distinct prefix/format (e.g. `🔬 ML`) so an anomaly advisory can
never be mistaken for an engine state transition or a system-status alert.

---

### 8. Orchestration Hooks

**Objective:** Wire the module into the existing engine runtime without coupling
to its decisions (Spec Section 7).

**Deliverables:**
- **Live hook:** after each engine cycle produces a snapshot, run Module 3
  (append) + Module 5 (score) + Module 6/7 on flag. Failure here must never
  break the engine loop — wrap so an ML error degrades to "no advisory," not an
  engine outage.
- **End-of-day hook:** enforce the strict order
  `feature-store sync (3) → retrain (4) → existing cleanup`. The cleanup step is
  the existing job, now proven to skip `ml_*` (Module 1).

**Constraints:** the ML module is a strict consumer of the engine, never a
dependency of it — the engine must run identically whether or not this module is
present or healthy.

---

### 9. (Optional / v2) Drift Monitor + Ensemble Toggle

**Objective:** Lightweight drift note (Spec 5.5) tracking the rolling anomaly-
score baseline; and an optional Isolation-Forest-plus-Mahalanobis two-voter mode
for the *decision* (Spec Open Decision 4).

**Constraints:** off by default. Drift notes are low-priority, not per-cycle
alerts. Do not build until Modules 1–8 are running and have accumulated at least
one full rolling window of data.
