# TASK-018 ML Live Scorer

**Goal:** Each cycle: load the latest model for the current config_type, score the standardized vector with Isolation Forest, persist to `ml_anomaly_scores`, and drive the debounced + hysteresis-gated anomaly state machine.

**Acceptance Criteria:**
- [ ] Loads latest `ml_model_registry` version for the cycle's config_type (cached; re-checked after EOD retrain)
- [ ] Standardizes with the model's stored scaler (never re-fits), scores, writes `ml_anomaly_scores` row (score, flag, warm-up status, top features, model version) every scored cycle
- [ ] No model / warming up → record score silently, status `WARMING_UP`, never flag
- [ ] `STALE`/`DISCONNECTED` cycle → skip scoring entirely (a broken feed is not a market anomaly)
- [ ] **Debounce:** advisory fires only on the *transition* into anomalous — never re-fires while it stays anomalous
- [ ] **Hysteresis:** enter anomalous only when score ≥ flag threshold; exit only when score < clear threshold; between the two → hold current state, no posts
- [ ] Optional minimum-dwell config (default off)
- [ ] Normal day → zero advisories; anomalous stretch → exactly one entry advisory (+ optionally one clear)

**Inputs:** ML Spec §5.2, §7, §7.1; Build Prompt 5.

**Output:** `src/aeolus/ml/scorer.py`.

**Edge Cases:** first cycle after go-live (no prior state); model version flips mid-session after retrain (state carries over, thresholds come from active model); score exactly at threshold; registry unreachable (degrade to no-op, never raise into caller).

**Depends on:** TASK-014, TASK-015, TASK-017.

**Global constraints:** see `docs/CONSTRAINTS.md` (incl. ML overlay constraints). Advisory only — never touches `market_state` or any engine table.

**Status:** DRAFT
