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
  TASK-008 Composite Scorer & State Machine
  ├─ config loader: expiry vs non-expiry weight/threshold tables (config/)
  ├─ weighted sum → NO_GO / PREPARE / GO
  ├─ hysteresis/debounce before flip
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

## Key data-flow rules

- Staleness detection lives **only** in ingestion; downstream modules trust `system_status`.
- DTE calculation lives **only** in context signals (TASK-007), exposed as a clean callable — consumed by engine (config selection) and outlook.
- Yesterday's profile-shape classification is a standalone enum/flag from TASK-007 — the Outlook's headline driver, never buried in a blended score.
- Reason strings flow one way: signal modules → explain util → engine → Discord. No module invents its own string format.
- Two output models, never mixed: Outlook = forecast prior (archetype probabilities, no NO-GO/PREPARE/GO vocabulary); Live State = the three states only.

## Dual configuration

`config/` holds two weight/threshold tables (expiry-day, non-expiry-day). Same formulas, same category structure — only weights/thresholds differ. Selected by DTE at runtime. Expiry day: gamma/OI walls weighted higher, IV bands recalibrated lower, GO bar raised. (Spec §8.)
