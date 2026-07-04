# TASK-015 ML Feature Extractor

**Goal:** Pure functions turning a `SignalSnapshot` into the fixed-order raw feature vector, and applying a *stored* scaler — never fitting one.

**Acceptance Criteria:**
- [ ] `FEATURE_ORDER` tuple + `FEATURE_SET_VERSION` constant — fixed, versioned feature ordering so a stored model always receives features in the order it trained on
- [ ] `extract_features(snapshot: SignalSnapshot) -> RawFeatureVector | None` — pulls the 5 category sub-scores, composite score, and the key raw readings (ML Spec §4 table) from `sub_scores`/`composite_score`/`raw_readings`
- [ ] `standardize(raw: RawFeatureVector, scaler: Scaler) -> list[float]` — applies stored μ/σ per feature; NO fit method anywhere in this module (fitting lives in TASK-017)
- [ ] Returns `None` (extraction refused) for rows with `system_status` `STALE`/`DISCONNECTED`
- [ ] Deterministic: same snapshot → same vector

**Inputs:** ML Spec §4, §5.1; Build Prompt 2; engine `raw_readings` key names (`iv_percentile_rank`, `vix_level_and_roc`, `expected_move_consumed_ratio`, `gex_regime`, `spot_distance_from_flip`, `pcr_level_and_roc`, `cvd_direction_and_divergence`).

**Output:** `src/aeolus/ml/features.py`.

**Edge Cases:** missing/None raw readings (safe_call failures upstream); nested raw_value payloads (level+RoC pairs) needing deterministic flattening; `config_type` selects the model — it is NOT a feature.

**Depends on:** TASK-014.

**Global constraints:** see `docs/CONSTRAINTS.md` (incl. ML overlay constraints). No clock-time logic — features are already session-relative by construction.

**Status:** DRAFT
