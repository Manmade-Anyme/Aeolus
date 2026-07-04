# AEOLUS — Architecture

Component map distilled from `files/AEOLUS_SYSTEM_SPEC.md` v1.0. Each component = one PM directive (`directives/TASK-###`). Build order = TASK number order (dependency-ordered).

## Component map

```
                       ┌─────────────────────────────────────────────┐
                       │ TASK-013 Scheduler / Orchestration          │
                       │ (only clock-aware component: "is mkt open") │
                       └───────┬──────────────┬──────────────┬───────┘
                        pre-open│        9:15–15:30│      post-close│
                               ▼              ▼                  ▼
                    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
                    │ TASK-009     │  │ Live loop    │  │ TASK-012     │
                    │ Pre-Market   │  │ (below)      │  │ Outcome-     │
                    │ Outlook      │  │              │  │ Label        │
                    └──────┬───────┘  └──────────────┘  │ Backfill     │
                           │                            └──────┬───────┘
                           ▼                                   ▼
                     daily_outlook                       outcome_labels
                                                       + realized archetype

Live loop (event-driven, continuous):

  TASK-002 Ingestion (Dhan API v2)
  ├─ WebSocket live feed + REST polled
  ├─ owns staleness → system_status (OK/STALE/DISCONNECTED)
  └─ feeds ↓
  TASK-003..007 Signal Modules — each sub-signal returns
  (raw_value, reference_band, sub_score, reason_string)
  ├─ 003 Volatility   (IV %ile, IV-RV, VIX, expected-move-consumed)
  ├─ 004 Gamma        (GEX/zero-gamma flip: sign + normalized magnitude)
  ├─ 005 OI Structure (PCR + RoC, buildup classes, walls, max-pain drift)
  ├─ 006 Order Flow   (CVD, absorption, volume-participation range)
  └─ 007 Context      (yesterday profile, gap type, DTE, futures-basis drift)
        │                                     │
        │             DTE flag ───────────────┘
        ▼
  TASK-008 Composite Scorer & State Machine — first module w/ real I/O
  ├─ config: EXPIRY_CONFIG/NON_EXPIRY_CONFIG (config/, ARES pydantic-settings pattern)
  ├─ equal-weighted category avg → weighted composite → NO_GO / PREPARE / GO
  ├─ N-cycle hysteresis before flip; safe_call isolates a crashing sub-signal
  └─ writes signal_snapshots (every cycle) + state_transitions (flips only)
        │
        ▼
  TASK-010 Explainability (shared templating util, used by 003–008)
        │
        ▼
  TASK-011 Discord Formatter & Dispatch
  ├─ outlook msg / state-transition msg / system-status msg (distinct format)
  └─ transition msg cites driving categories + confirm/diverge vs Outlook
```

## Storage (TASK-001, Supabase/Postgres)

See `DATA_MODEL.md`. Live and backtest data are the same append-only log.

## Module boundaries → source layout

| Component | Directory | Directive |
|---|---|---|
| Schema/migrations | (supabase migrations) | TASK-001 |
| Dhan ingestion | `src/aeolus/ingestion/` | TASK-002 |
| Signal categories ×5 | `src/aeolus/signals/` | TASK-003..007 |
| Composite + state machine | `src/aeolus/engine/` | TASK-008 |
| Pre-market outlook | `src/aeolus/outlook/` | TASK-009 |
| Reason templating | `src/aeolus/explain/` | TASK-010 |
| Discord output | `src/aeolus/output/` | TASK-011 |
| Backfill job | `src/aeolus/jobs/` | TASK-012 |
| Scheduler | `src/aeolus/scheduler/` | TASK-013 |
| ML anomaly overlay | `src/aeolus/ml/` | TASK-014..022 |

## ML anomaly overlay (TASK-014..022 — advisory, never a dependency)

Spec: `files/AEOLUS_ML_ANOMALY_SPEC.md`; build order: `files/AEOLUS_ML_ANOMALY_BUILD_PROMPTS.md`. Unsupervised Isolation Forest overlay answering "is today structurally unlike any normal day learned?" — label-free, deployed before any supervised model while `outcome_labels` accumulates.

```
  Scheduler (TASK-013)
  ├─ live loop: engine.run_cycle → MLHooks.on_cycle (TASK-021, failure-isolated)
  │    └─ append to ml_feature_store (016) → score vs latest model (018)
  │       └─ on ANOMALY_ENTER/CLEAR transition only (debounce+hysteresis):
  │          top-|z| attribution (019) → 🔬 ML Discord advisory (020)
  └─ post-close: backfill (012) → MLHooks.on_end_of_day (021):
       feature-store sync (016) → retrain per config (017)
       → RetentionJob (014, scheduler-owned, runs even if ML disabled):
         trim signal_snapshots + ml_anomaly_scores >90d, prune registry to last 30/config;
         never touches ml_feature_store / transitions / outlook / outcome_labels

  TASK-014: ml_feature_store / ml_model_registry / ml_anomaly_scores + ML_PROTECTED_TABLES
  TASK-015: SignalSnapshot → fixed-order versioned feature vector; stored-scaler standardize
  TASK-022 (v2, deferred): drift note + IF+Mahalanobis ensemble toggle
```

Rules: advisory only (never writes engine tables, never alters market_state); two independent models (EXPIRY/NON_EXPIRY); IF decides, z-scores explain; deterministic templated reasons; STALE/DISCONNECTED never scored/trained; warm-up gating before any flag (`WARMING_UP` until ≥10×n_features samples AND ≥15 trading days per config). Engine runs identically with `ml_hooks=None`.

## Key data-flow rules

- Staleness detection lives **only** in ingestion; downstream modules trust `system_status`.
- DTE calculation lives **only** in context signals (TASK-007), exposed as a clean callable — consumed by engine (config selection) and outlook.
- Yesterday's profile-shape classification is a standalone enum/flag from TASK-007 — the Outlook's headline driver, never buried in a blended score.
- Reason strings flow one way: signal modules → explain util → engine → Discord. No module invents its own string format.
- Two output models, never mixed: Outlook = forecast prior (archetype probabilities, no NO-GO/PREPARE/GO vocabulary); Live State = the three states only.
- `volatility.implied_expected_move` (VIX-based, TASK-008 §7a) is a shared function — both the live engine (`expected_move_consumed_ratio`'s denominator) and the pre-market Outlook (`straddle_level_vs_history`) call the same one, not two copies of the same formula.

## Dual configuration

`config/tuning.py` (schema) + `config/profiles.py` (`EXPIRY_CONFIG`/`NON_EXPIRY_CONFIG` — two complete, hardcoded instances, ARES's `pydantic-settings` pattern, not YAML). Same formulas, same category structure — only weights/thresholds differ. Selected by `dte()==0` at runtime, TASK-008's job. Expiry day: gamma/OI walls weighted higher, IV bands recalibrated lower, GO bar raised. (Spec §8.)
