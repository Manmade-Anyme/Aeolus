# TASK-019 ML Explainability / Top-Feature Attribution

**Goal:** For a flagged vector, rank features by |z_i| and emit a deterministic templated reason string naming the top 2–3 driving dimensions with signed z-scores.

**Acceptance Criteria:**
- [ ] `top_contributors(z_vector, k) -> list[(feature_name, z)]` ranked by |z|, deterministic tie-break (feature order)
- [ ] `anomaly_reason(contributors, score, model_version) -> str` — templated from numbers, e.g. `anomalous — driven by vix_level_and_roc (+3.2σ), gex_regime (−2.8σ)`
- [ ] Identical vector + model → identical string (property test)
- [ ] Explanation NEVER influences the flag decision — that is TASK-018's Isolation Forest verdict alone

**Inputs:** ML Spec §3.1, §5.3; Build Prompt 6; TASK-010 templating conventions (`src/aeolus/explain/reason.py`) for style consistency.

**Output:** `src/aeolus/ml/explain.py`.

**Edge Cases:** ties in |z|; fewer than k features with |z| above noise; z computed from a σ that was near-zero in the training window (guard in scaler).

**Depends on:** TASK-015, TASK-018 (consumes its z-vector).

**Global constraints:** see `docs/CONSTRAINTS.md` (incl. ML overlay constraints). Deterministic reason strings — never LLM-narrated.

**Status:** APPROVED — planning merged via [PR #17](https://github.com/dubeyshantanu2/Aeolus/pull/17) (commit `909de7d`), 2026-07-04
