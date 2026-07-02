# AEOLUS — Data Model (Supabase / Postgres)

Conceptual schema from Spec §9. Concrete DDL: `supabase/migrations/0001..0005_*.sql` (ADR: `directives/adr/TASK-001_supabase-schema.md`). Typed row models: `src/aeolus/storage/models.py`. Live data and backtest data are the **same append-only log** — no separate synthetic backtest table.

OPEN_DECISIONS #3 resolved 2026-07-03: no historical backfill required, live-forward only. See "Known limitation" below.

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

Accumulates from go-live forward only. Pre-launch backtesting needs a separate historical source (NSE bhavcopy / paid vendor) — resolved out of scope (OPEN_DECISIONS #3, 2026-07-03).

## Concrete DDL reference

5 migration files, applied in order via Supabase CLI (`supabase db push`), idempotent on re-run.

| File | Contents |
|---|---|
| `0001_enums.sql` | `market_state`, `system_status`, `config_type`, `day_archetype`, `outcome_direction` — native Postgres enum types, so `market_state` and `system_status` are structurally distinct (a value of one can never be assigned to a column typed as the other) |
| `0002_signal_snapshots.sql` | `signal_snapshots` — `raw_readings`/`sub_scores`/`reasons` as `jsonb` (category roster grows across TASK-003..007, avoids a migration per signal change); `composite_score`, `market_state`, `system_status`, `config_type`, `dte` as typed top-level columns. Indexes: `ts`, `config_type` |
| `0003_state_transitions.sql` | `state_transitions` — `trigger_categories` as `jsonb` list. Index: `ts` |
| `0004_daily_outlook.sql` | `daily_outlook` — `trend_exhaustion_flag boolean` as its own column (never folded into `contributing_inputs`); `realized_archetype` nullable, backfilled by TASK-012. Unique + index: `session_date` |
| `0005_outcome_labels.sql` | `outcome_labels` — FKs to `signal_snapshots`/`state_transitions` use `ON DELETE SET NULL` (append-only log, never cascade-delete label data); `horizon_minutes` constrained to `{15, 30, 60}`; check constraint requires at least one source FK |

All timestamps are `timestamptz`, stored UTC — IST conversion is a presentation concern (TASK-011 Discord formatter), not storage.

Typed models (`src/aeolus/storage/models.py`): `SignalSnapshot`, `StateTransition`, `DailyOutlook`, `OutcomeLabel` — one pydantic model per table, each with a `TABLE` class var naming its table. Signal modules (TASK-003..007) never write here directly; they return `(raw_value, reference_band, sub_score, reason_string)` to the composite scorer (TASK-008), the sole writer of `signal_snapshots`/`state_transitions`.

Tests: `tests/storage/test_models.py` (pydantic contract, runs always) and `tests/storage/test_migrations.py` (real DDL against Postgres, skipped unless `TEST_DATABASE_URL` is set — see file docstring for local setup).
