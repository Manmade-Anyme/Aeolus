# Architecture Decision Record — TASK-001

**Directive:** `directives/TASK-001_supabase-schema.md`
**Status:** APPROVED
**Date:** 2026-07-03

## Problem

Stand up the four append-only Postgres tables (`signal_snapshots`, `state_transitions`, `daily_outlook`, `outcome_labels`) behind Supabase, as plain SQL migrations, matching Spec §9 / `docs/DATA_MODEL.md`. No ORM model layer yet — later tasks (008, 011, 012) read/write these tables directly via the `supabase-py` client using raw dict payloads validated by pydantic schemas defined here. This is schema-and-migrations only; no ingestion, no scoring logic.

OPEN_DECISIONS #3 resolved 2026-07-03: no historical backfill. Schema accumulates from go-live forward only — no separate synthetic/backtest table, no historical-source ingestion path. This simplifies TASK-001 scope to exactly the four tables as specified.

## Decision

Plain numbered SQL migration files under `supabase/migrations/`, applied via the Supabase CLI (`supabase db push` / `supabase migration up`) — not a Python migration framework (alembic etc.), since Supabase's own CLI is the standard tool for this and keeps schema ownership in SQL, not in application code. Enums are native Postgres `CREATE TYPE ... AS ENUM`, not check constraints, so `system_status` and `market_state` are structurally distinct types — a value of one can never be assigned to a column typed as the other, which enforces the system_status ≠ market_state invariant at the schema level rather than relying on application discipline.

`signal_snapshots` uses one `jsonb` column (`raw_readings`) to hold the per-category raw-value/reference-band pairs rather than a wide fixed-column table. Category set will grow/shift across TASK-003..007 as signals get built; a fixed-column design would mean a migration per signal change. `jsonb` keeps the retroactive-recomputability requirement (raw data preserved) without coupling the schema to the exact signal roster. Per-category sub-scores get the same treatment (`sub_scores jsonb`) for the same reason. Composite score, state, system_status, config_type, DTE stay as typed top-level columns since they're stable, queried-on-often, and indexed.

Alternative considered: fully normalized per-category tables (one row per category per cycle). Rejected — massively multiplies row count for no query benefit at this scale (single-instrument, ~few-second cadence), and complicates the "one row = one computation cycle" mental model the spec uses throughout.

All timestamp columns are `timestamptz` (never naive `timestamp`), stored in UTC, per the IST-unambiguous-storage requirement — conversion to IST is a presentation concern (Discord formatter, TASK-011), not a storage concern.

## Component Boundaries

| File | Responsibility |
|------|---|
| `supabase/migrations/0001_enums.sql` | `market_state`, `system_status`, `config_type`, `day_archetype` enum types |
| `supabase/migrations/0002_signal_snapshots.sql` | `signal_snapshots` table + indexes |
| `supabase/migrations/0003_state_transitions.sql` | `state_transitions` table + indexes |
| `supabase/migrations/0004_daily_outlook.sql` | `daily_outlook` table + indexes |
| `supabase/migrations/0005_outcome_labels.sql` | `outcome_labels` table + indexes |
| `supabase/migrations/0006_fix_outcome_labels_source_check.sql` | Drops `outcome_labels_has_source` CHECK — conflicted with `ON DELETE SET NULL`, see Amendment |
| `src/aeolus/storage/models.py` | Pydantic models mirroring each table row, for typed read/write by later tasks |
| `src/aeolus/storage/__init__.py` | Package marker, re-exports models |

## API Contracts

```python
# src/aeolus/storage/models.py

class SignalSnapshot(BaseModel):
    """One row per computation cycle. Written by TASK-008 (composite scorer)."""
    id: UUID
    ts: datetime  # timestamptz, UTC
    session_date: date
    config_type: Literal["EXPIRY", "NON_EXPIRY"]
    dte: int
    raw_readings: dict[str, Any]   # {category: {raw_value, reference_band}}
    sub_scores: dict[str, float]   # {category: sub_score}
    composite_score: float
    market_state: Literal["NO_GO", "PREPARE", "GO"]
    system_status: Literal["OK", "STALE", "DISCONNECTED"]
    reasons: dict[str, str]        # {category: reason_string}

class StateTransition(BaseModel):
    """Written only on confirmed, debounced state flips. Written by TASK-008."""
    id: UUID
    ts: datetime
    from_state: Literal["NO_GO", "PREPARE", "GO"]
    to_state: Literal["NO_GO", "PREPARE", "GO"]
    trigger_categories: list[str]
    reason: str

class DailyOutlook(BaseModel):
    """One row per trading day. Written pre-market by TASK-009, backfilled by TASK-012."""
    id: UUID
    session_date: date
    predicted_archetype: str
    archetype_confidence: float
    contributing_inputs: dict[str, Any]
    trend_exhaustion_flag: bool     # own field, never folded into contributing_inputs
    straddle_level_vs_history: float
    realized_archetype: str | None  # null until TASK-012 backfill

class OutcomeLabel(BaseModel):
    """Backfilled enrichment, never written live. Written by TASK-012 only."""
    id: UUID
    snapshot_id: UUID | None
    transition_id: UUID | None
    horizon_minutes: Literal[15, 30, 60]
    straddle_price_change: float
    realized_move: float
    direction: Literal["UP", "DOWN", "FLAT"]
```

Signal modules (TASK-003..007) do not touch this layer directly — they return the standard `(raw_value, reference_band, sub_score, reason_string)` tuple to the composite scorer (TASK-008), which is the sole writer of `signal_snapshots`/`state_transitions`.

## Performance / Failure Modes

- Migrations must be idempotent under re-run: enum/table creation uses `CREATE TYPE IF NOT EXISTS` guards (via `DO $$ ... EXCEPTION WHEN duplicate_object $$` block, since Postgres lacks native `CREATE TYPE IF NOT EXISTS`) and `CREATE TABLE IF NOT EXISTS`.
- No FK cascade deletes — this is an append-only audit log; nothing should ever delete rows. `outcome_labels.snapshot_id`/`transition_id` are nullable FKs with `ON DELETE SET NULL`, not `CASCADE`, so an accidental snapshot delete doesn't silently destroy label data.
- Indexes: `signal_snapshots(ts)`, `signal_snapshots(config_type)` per acceptance criteria; `state_transitions(ts)`; `daily_outlook(session_date)` unique; `outcome_labels(snapshot_id)`, `outcome_labels(transition_id)`.
- No latency budget at this layer — pure DDL, applied once per environment setup, not on the hot path.

## Definition of Done

- [x] Integration-style tests against the public contracts above — `tests/storage/test_supabase_integration.py`, live against the actual Supabase project, no internal mocking
- [x] Migrations apply cleanly to a fresh Supabase project, and re-applying them is a no-op — verified via `create table/type if not exists` guards; live-applied 2026-07-03
- [x] `system_status` and `market_state` are separate Postgres enum types (schema-level type check, not just app-level)
- [x] Round-trip test: write a `SignalSnapshot` with arbitrary `raw_readings`/`sub_scores` payload, read it back, confirm composite is recomputable from stored raw data alone
- [x] `daily_outlook.trend_exhaustion_flag` exists as its own boolean column, not nested in JSON
- [x] `docs/DATA_MODEL.md` updated with concrete DDL reference alongside the conceptual schema
- [x] Constraint check: no per-signal veto (n/a — no scoring logic here), no clock logic (n/a), deterministic reasons (schema stores `reason_string` as plain text, never generated here), polarity correct (n/a — enums only, no interpretation)

## Amendment (2026-07-03, post-merge)

**DDL application method, corrected.** The Decision section above assumed `supabase db push` via the Supabase CLI. In practice, only `SUPABASE_URL`/`SUPABASE_KEY` (REST API anon key) were available — no Supabase CLI login, no direct Postgres connection string/DB password. This project shares a Supabase instance with the Ares project, which already established the working pattern (`~/Documents/Obsidian/Projects/Ares/07_Storage.md`): DDL applied manually via the Supabase Dashboard SQL Editor (one-time, human-run), runtime reads/writes via `supabase-py` + anon key, with **RLS disabled** on app tables since the anon key is the only credential available at runtime. `signal_snapshots`, `state_transitions`, `daily_outlook`, `outcome_labels` now follow this same convention — RLS disabled via the same SQL Editor pass that created them.

**Bug found via live testing, fixed in `0006_fix_outcome_labels_source_check.sql`.** The original `outcome_labels_has_source` CHECK constraint (0005) — "at least one of snapshot_id/transition_id must be set" — conflicts with the `ON DELETE SET NULL` FK behavior from the same file: deleting a `signal_snapshots` row referenced by a label with *only* `snapshot_id` set causes Postgres to attempt `SET NULL`, which then trips the CHECK (both columns now null), and the whole `DELETE` is rejected. Caught by `tests/storage/test_supabase_integration.py::test_outcome_label_snapshot_delete_sets_null_not_cascade` against the real database — this is exactly the scenario the model-only test suite (pydantic, no DB) couldn't have caught, since pydantic validation only runs at construction time, not on DB-side cascade behavior.

Fix: drop the CHECK constraint (`0006`), applied live 2026-07-03. The "at least one source" invariant now lives **only** in `OutcomeLabel._has_source` (pydantic, `src/aeolus/storage/models.py`) — acceptable since TASK-012 is the sole writer of this table and always goes through that model. Trade-off: a direct/manual insert bypassing the pydantic layer could now create a sourceless row; no such path exists in the current design.

**Test suite, corrected.** `tests/storage/test_migrations.py` (psycopg-based, direct-Postgres-connection assumption) was written against a method never actually usable here and is replaced by `tests/storage/test_supabase_integration.py` (supabase-py client, same access pattern as production code will use). `psycopg` dropped from dev dependencies; `python-dotenv` added (loads `.env` for test credentials).
