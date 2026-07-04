# AEOLUS — Anomaly Detection ML Module
**Specification v1.0**
*Companion to AEOLUS_SYSTEM_SPEC.md*
*Last updated: 4 July 2026*

> This module is an **advisory overlay**, not part of the deterministic engine.
> It answers one question the engine cannot: *"is today structurally unlike any
> normal day I have learned?"* It never writes to engine tables, never alters
> NO-GO/PREPARE/GO, and never gates a trade. It emits its own score, its own
> reason, its own Discord message. The deterministic engine stays ground truth.

---

## 1. Purpose

The deterministic engine classifies every cycle into one of three known states.
It has no concept of *"this doesn't look like any regime I know — be extra
cautious."* This module supplies exactly that: a label-free confidence overlay
that flags days/cycles whose signal structure is abnormal versus everything the
system has learned — the event days, structural breaks, and one-off conditions
that historically break hand-tuned systems.

It is deployed first (before any supervised model) specifically because it needs
**no labels** — it works from the raw signal vectors from day one, while the
`outcome_labels` table is still accumulating for later XGBoost/logistic work.

## 2. Scope & Boundaries

| In scope | Out of scope |
|---|---|
| Unsupervised anomaly scoring of the live signal vector | Any labeled/supervised prediction (that is the later XGBoost/logistic phase) |
| Own persistent feature store + model registry in Supabase | Reading/writing any deterministic-engine decision |
| Own Discord advisory output | Gating, sizing, or entry logic |
| Isolation Forest (primary) + Mahalanobis/z-score (explainability only) | Deep learning, autoencoders (over-engineered for the data volume) |
| Periodic batch retraining on a rolling window | True tick-by-tick online learning (optional v2) |

## 3. Core Design Decisions (read before building)

### 3.1 Isolation Forest is primary; Mahalanobis is explainability only
The market has **multiple normal regimes** (trend-normal, range-normal). A single
Mahalanobis center would flag legitimate-but-different regimes as anomalous. So:
- **Isolation Forest** decides *whether* a vector is anomalous — it handles
  multimodal "normal" natively.
- **Per-feature z-score / Mahalanobis contribution** is used only to *explain*
  which dimensions are extreme, feeding the deterministic reason string. It does
  not decide the flag.

### 3.2 The module owns its training data — cleanup must not touch it
The end-of-day cleanup that trims `signal_snapshots` would destroy this module's
training history if it read only engine tables. Therefore:
- The module maintains its **own persistent feature store** (`ml_feature_store`).
- It copies the numeric feature vectors it needs into that store **before**
  cleanup runs.
- All `ml_*` tables are on the cleanup **denylist** — the cleanup job is
  explicitly configured never to touch them.
- **Hard ordering constraint (end of day):**
  `feature-store sync → retrain → cleanup`. Never reorder. If cleanup ran first,
  retrain would still work (the store already holds its copy), but the sync must
  precede cleanup so nothing is lost in the first place.

### 3.3 Separate models per config (expiry vs non-expiry)
Expiry-day structure differs enough that a single model would flag every Tuesday
as anomalous. Two independently-fitted models, selected at scoring time by the
same DTE/config flag the engine already computes (Context module).

### 3.4 Time-agnostic, matching the engine
No clock-time conditioning. The engine's signals are already computed in
session-relative terms (volume vs rolling average, rate-of-change), so the
feature vectors are naturally time-normalized. Keeping the anomaly model
time-agnostic preserves the engine's no-hardcoded-time principle.

### 3.5 "Learning" = scheduled retraining on a growing/rolling window
Isolation Forest is a batch method — there is no true incremental fit. The
module "learns and upgrades" by **retraining end-of-day** on the accumulating
feature store. Early on the window is *all* history; once enough days accrue it
becomes a rolling window (default 60 trading days, configurable) so the model
adapts to drift without forgetting regime diversity. Each retrain is versioned.

## 4. Data — What It Trains From

**Feature source:** the numeric content of each `signal_snapshots` row, copied
into `ml_feature_store`. The feature vector per cycle:

| Group | Features |
|---|---|
| Category sub-scores | Volatility, Gamma, OI-Structure, Order-Flow, Context sub-scores |
| Composite | Composite score |
| Key raw readings | IV percentile, India VIX + rate of change, PCR + rate of change, GEX magnitude, spot-to-flip distance, ATM straddle expected-move-consumed ratio, CVD-vs-price divergence measure |

Notes:
- **Standardize every feature** (store the scaler's mean/σ per model version in
  the registry; apply the *same* stored scaler at live-scoring time — never
  re-fit the scaler on a single live vector).
- Raw readings are on wildly different scales, so standardization is mandatory,
  not optional.
- `config_type` (expiry/non-expiry) selects *which* model, it is not itself a
  feature.
- Exclude `system_status`-degraded rows (`STALE`/`DISCONNECTED`) from both
  training and scoring — a broken feed is not a market anomaly.

**Two data sources feeding training, as requested:**
1. **Flowing (live):** each cycle's vector is scored immediately and appended to
   the feature store.
2. **Saved (Supabase):** end-of-day retraining reads the full rolling window from
   `ml_feature_store` — i.e. it learns from the accumulated saved data, not just
   today's.

## 5. Calculations

### 5.1 Feature standardization
For feature *i*: `z_i = (x_i − μ_i) / σ_i`, where `μ_i`, `σ_i` come from the
training window and are frozen into the model version's scaler record.

### 5.2 Isolation Forest anomaly score (the decision)
Standard Isolation Forest: build an ensemble of random isolation trees; the
anomaly score for point *x* is

```
s(x, n) = 2^( − E[h(x)] / c(n) )
```

where `E[h(x)]` is the mean path length to isolate *x* across trees and `c(n)`
is the expected path length normalizer for *n* samples. Score → 1 means easily
isolated (anomalous); score ≈ 0.5 means normal.

**Flag threshold:** do **not** rely on the raw contamination default. Calibrate
the flag threshold as an **empirical percentile** of training-set scores
(default: flag the top 5%), recomputed on every retrain. This adapts the cutoff
to the current regime distribution rather than assuming a fixed anomaly rate.

### 5.3 Per-feature explanation (Mahalanobis / z-score, explanation only)
Once flagged, rank features by standardized deviation |z_i| (or Mahalanobis
per-term contribution if you want covariance-aware attribution). Report the top
2–3 driving dimensions in the reason string, e.g. *"anomalous — driven by VIX
rate-of-change (+3.2σ) and GEX magnitude (−2.8σ)."* Deterministic: same inputs →
same explanation, no free-text generation.

### 5.4 Warm-up gating
Two thresholds before scores are trusted:
- **Statistically fittable:** at least ~10× n_features standardized samples in
  the config's window (covariance/forest is estimable).
- **Regime-representative:** the window has spanned enough distinct day-types
  (heuristic: N ≥ 15–20 trading days for that config).
Until both are met the module emits status `WARMING_UP` and posts no anomaly
flags (it still records scores silently so you can inspect calibration).

### 5.5 Drift monitoring (lightweight)
Track the rolling distribution of daily anomaly scores. A sustained upward shift
in the baseline score means the world has moved and the model needs the rolling
window to catch up — surface this as a low-priority note, not a per-cycle alert.

## 6. Supabase Schema (all `ml_*`, all cleanup-protected)

- **`ml_feature_store`** — one row per scored cycle: timestamp, config_type,
  the standardized feature vector (and raw values), source snapshot id. This is
  the persistent training corpus that survives cleanup.
- **`ml_model_registry`** — one row per trained model version: config_type,
  serialized Isolation Forest, scaler params (μ/σ per feature), flag threshold,
  training-window bounds, sample count, trained-at timestamp, version id.
- **`ml_anomaly_scores`** — one row per scored cycle: timestamp, config_type,
  raw anomaly score, flag (bool), warm-up status, top contributing features +
  their z-scores, model version used. Advisory output log.

Cleanup config change: add `ml_feature_store`, `ml_model_registry`,
`ml_anomaly_scores` to the cleanup job's protected/denylist set. This is a
required change to the existing cleanup process, not a new table alone.

## 7. Runtime Behavior

**Live (every cycle):**
1. Read current signal vector (skip if `STALE`/`DISCONNECTED`).
2. Select model by config_type; if none/warming up → record score, emit
   `WARMING_UP`, no flag.
3. Standardize with the stored scaler, score via Isolation Forest.
4. If score ≥ flag threshold → compute top-feature explanation → write
   `ml_anomaly_scores` → emit Discord advisory. **Posting is debounced and
   hysteresis-gated (Section 7.1) so a sustained or boundary-hovering anomaly
   posts once, never every cycle.**
5. Always append the vector to `ml_feature_store`.

### 7.1 Anti-spam: debounce + hysteresis (mandatory)

Two independent guards, both required — the module must be quiet on normal days
and must not chatter on weird ones:

- **Debounce (stops re-posting an ongoing anomaly):** a flag posts only on the
  *transition* into anomalous, not on every cycle it remains anomalous. While it
  stays flagged, no further posts.
- **Hysteresis (stops flapping at the boundary):** use two thresholds, not one.
  Enter the anomalous state only when score crosses the upper flag threshold
  (e.g. 0.74); exit only when it drops below a lower clear threshold (e.g. 0.66).
  A score oscillating between the two stays in its current state and posts
  nothing. This mirrors the engine's own state-machine hysteresis.
- **Optional minimum dwell:** a config value for the minimum cycles a state must
  hold before it can flip again, as a final backstop. Default off; enable if any
  chatter survives the two guards above.

Net effect: a normal day posts **zero** ML messages. A genuinely anomalous
stretch posts **one** advisory on entry and (optionally) one clear on exit — not
a stream.

**End of day (strict order):**
1. **Feature-store sync** — ensure every cycle's vector is persisted.
2. **Retrain** — refit Isolation Forest + scaler + threshold per config on the
   rolling window; write a new `ml_model_registry` version.
3. **Cleanup** — the existing junk-trimming job runs last, `ml_*` untouched.

## 8. Discord Output

Distinct format from engine messages (different emoji/prefix, e.g. `🔬 ML`):
- **Anomaly advisory:** flag, anomaly score, top contributing dimensions with
  z-scores, model version, and an explicit *"advisory only — does not change
  engine state"* footer. Posted once on entry (Section 7.1), not per cycle.
- **Anomaly cleared (optional):** one line when score drops below the clear
  threshold and the anomalous state exits.
- **Warm-up daily progress:** a single low-key line per day while a config's
  model is still warming up (e.g. *"still learning — day 7 of ~18"*), so it's
  visibly alive during the first stretch rather than appearing dead. One line
  per day maximum, not per cycle.
- **Warm-up go-live notice:** posted once when a config's model first becomes
  active (leaves warm-up), so you know when to start trusting it.

## 9. Explicit Non-Goals
- Not a signal generator, not a gate, not a state-changer — advisory only.
- No supervised prediction in this module (that is the later phase).
- No deep learning / autoencoders in v1.
- No true online learning in v1 (batch retrain only).
- Never reads-then-writes engine tables; strictly read-only against
  `signal_snapshots`.

## 10. Open Decisions (confirm before build)
1. **Rolling window length** — default 60 trading days once past warm-up.
   Confirm, or set shorter (faster drift adaptation) / longer (more regime
   diversity).
2. **Flag percentile** — default top 5%. Confirm sensitivity; higher % = more
   flags = more noise.
3. **Retrain cadence** — default daily end-of-day. Weekly is cheaper and still
   fine given batch nature — confirm preference.
4. **Isolation Forest + Mahalanobis ensemble for the *decision*** — v1 uses IF
   alone for the decision. If you later want covariance-aware detection as a
   second voter (not just explanation), that's a v2 toggle.
