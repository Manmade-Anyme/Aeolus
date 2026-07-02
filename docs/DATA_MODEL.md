# AEOLUS — Data Model (Supabase / Postgres)

Conceptual schema from Spec §9. Concrete DDL is TASK-001's deliverable (ADR first). Live data and backtest data are the **same append-only log** — no separate synthetic backtest table.

## Enums

- `market_state`: `NO_GO` | `PREPARE` | `GO`
- `system_status`: `OK` | `STALE` | `DISCONNECTED` — **separate enum, separate concern.** Feed problems never masquerade as a market read.
- `config_type`: `EXPIRY` | `NON_EXPIRY`
- Day archetype (Spec §4): clean_trend | grinding_trend | pinned_range | choppy_range | breakout_transition | event_gap | double_distribution

## Tables

### `signal_snapshots` — one row per computation cycle
The ML feature set by construction.

| Field group | Contents |
|---|---|
| identity | timestamp, session date, config_type, DTE, day context |
| raw readings | per-category raw values + reference bands — **enough to recompute the composite retroactively if weights change** (hard constraint, Build Prompt 1) |
| scores | per-category sub-scores, composite score |
| state | market_state, system_status |
| explain | per-category reason strings |

Indexes: timestamp, config_type.

### `state_transitions` — thin log, only actual state changes
What gets posted to Discord.

- entry state, exit state, trigger category/categories, reason, timestamp
- written only on **confirmed, debounced** flips — never every cycle

### `daily_outlook` — one row per trading day
The labeled dataset for the pre-market forecasting model.

- predicted archetype distribution + primary/secondary call + confidence
- contributing inputs, explicitly including:
  - **prior-day trend-exhaustion flag** (own field, not folded into blend)
  - straddle-premium-level-vs-recent-history reading
- `realized_archetype` — backfilled after close by TASK-012

### `outcome_labels` — backfilled enrichment, never live
For snapshots/transitions: forward realized outcome at **+15/+30/+60 min** — straddle price change, realized move, direction. Written by TASK-012 only (labels don't exist at signal-time).

## Scoreable datasets this produces (Spec §11)

1. **Live-state accuracy** — did GO precede real premium movement; did NO-GO precede quiet stretches.
2. **Outlook accuracy** — did forecast archetype match realized day-type.

These move weights/thresholds from judgment-calibrated to empirically-calibrated over time.

## Known limitation

Accumulates from go-live forward only. Pre-launch backtesting needs a separate historical source (NSE bhavcopy / paid vendor) — out of scope unless OPEN_DECISIONS #3 resolves otherwise.
