# AEOLUS — Project Instructions

NIFTY regime & premium-movement forecasting system. "Weather app for the market, not a signal generator." Full spec: `files/AEOLUS_SYSTEM_SPEC.md`. Build prompts (dependency-ordered): `files/AEOLUS_BUILD_PROMPTS.md`. ML anomaly overlay (TASK-014..022): spec `files/AEOLUS_ML_ANOMALY_SPEC.md`, prompts `files/AEOLUS_ML_ANOMALY_BUILD_PROMPTS.md` — advisory only, never gates the engine; ML-specific constraints in `docs/CONSTRAINTS.md`.

## Hard Constraints — apply to ALL code in this repo

1. **No per-signal veto.** NO-GO must only ever emerge from the composite score landing low. Never `if signal_x < threshold: force NO_GO`.
2. **No clock-time interpretation logic.** The scheduler decides *when* to run; signals never branch on *what time it is*. "Is market open" gating lives only in the scheduler (Module 13).
3. **Deterministic reason strings.** Templated from `(raw_value, reference_band, sub_score)`. Never LLM-narrated, never free-text.
4. **Polarity: GO = favorable for directional option BUYING** (movement/IV expansion). NO-GO = quiet/pinned, sit out. This is the *inverse* of premium-selling regime tools. Never copy polarity from reference tools.

## Standard Signal Contract

Every sub-signal function in Modules 3–7 returns:
```python
(raw_value, reference_band, sub_score, reason_string)
```
This tuple feeds both the composite scorer and explainability. Do not deviate per module.

## Pipeline (Global Development Pipeline — Design-First)

- One directive per module: `directives/TASK-###_*.md` (001–013 engine, all merged; 014–022 ML overlay, DRAFT).
- Before implementing a TASK: Architect writes ADR at `directives/adr/TASK-###_*.md` from the directive + spec. Human approves ADR before code.
- Build order = TASK number order. Do not start TASK-008 before 003–007 exist; do not start TASK-011 before 008. ML: 014→021 in order; 018 needs 017; 021 needs 016–020; 022 is v2/deferred.
- Debug report → `reports/debug/TASK-###_debug-report.md`; QA report → `reports/qa/TASK-###_qa-report.md` (templates in those folders).
- After QA pass: update `CHANGELOG.md`, update `docs/`, sync Obsidian (`~/Documents/Obsidian/Projects/Aeolus`), then commit on `feature/TASK-###-*` branch, PR to `main`. Never merge PRs yourself.

## Layout

```
src/aeolus/
  ingestion/   # TASK-002 Dhan API v2 client, staleness/heartbeat owner
  signals/     # TASK-003..007 volatility, gamma, oi_structure, order_flow, context
  engine/      # TASK-008 composite scorer, state machine, debounce, config loader
  outlook/     # TASK-009 pre-market outlook generator
  explain/     # TASK-010 reason-string templating utility
  output/      # TASK-011 discord formatter/dispatch
  jobs/        # TASK-012 outcome-label backfill
  scheduler/   # TASK-013 orchestration; only place aware of clock/market hours
  ml/          # TASK-014..022 anomaly overlay: models, features, store, trainer,
               #   scorer, explain, output, hooks (advisory only, engine-independent)
config/        # weight/threshold tables: expiry vs non-expiry (TASK-008)
docs/          # ARCHITECTURE, CONSTRAINTS, DATA_MODEL, OPEN_DECISIONS
```

## Key domain facts

- NIFTY weekly/monthly expiry = **Tuesday** (NSE, since 1 Sep 2025); holiday → previous trading day. Compute DTE from holiday-aware calendar, never hardcode weekday.
- Dual config (expiry-day vs non-expiry-day): same signal formulas, different weight/threshold tables only.
- Hysteresis/debounce mandatory before any state flip or Discord post.
- `system_status` (OK/STALE/DISCONNECTED) is separate from `market_state` (NO_GO/PREPARE/GO). Feed problems must never masquerade as NO-GO.
- Storage: Supabase (Postgres), 4 engine tables + 3 `ml_*` tables, bounded by a 90-day RetentionJob (`ml_feature_store`/`state_transitions`/`daily_outlook`/`outcome_labels` never trimmed) — see `docs/DATA_MODEL.md`.
- Open decisions in `docs/OPEN_DECISIONS.md` must be resolved with the human before the affected module is built.
