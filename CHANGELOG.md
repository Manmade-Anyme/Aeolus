# Changelog

All notable changes to AEOLUS. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- 2026-07-03 — Project scaffold: folder structure, pipeline docs (`docs/`), PM directives TASK-001..013, ADR/report templates, project `CLAUDE.md`. No code yet.
- 2026-07-03 — TASK-001 Supabase schema: 5 migration files (`supabase/migrations/`) for `signal_snapshots`, `state_transitions`, `daily_outlook`, `outcome_labels` + enum types; typed pydantic models (`src/aeolus/storage/models.py`); ADR at `directives/adr/TASK-001_supabase-schema.md`. `system_status`/`market_state` enforced as structurally distinct Postgres enum types. Model-layer tests pass (16/16); DB-level DDL tests written, pending `TEST_DATABASE_URL`.
- 2026-07-03 — TASK-001 live DB verification: schema applied to the real (shared, Ares) Supabase project via SQL Editor; access pattern switched to `supabase-py` + anon key (RLS disabled), matching Ares convention — direct-Postgres/CLI approach wasn't available in this environment. New live test suite `tests/storage/test_supabase_integration.py` (8/8 pass) replaces the unusable psycopg-based one.

### Fixed
- 2026-07-03 — TASK-001: `outcome_labels_has_source` CHECK constraint conflicted with the `ON DELETE SET NULL` FK on the same table, blocking deletes of referenced `signal_snapshots` rows entirely. Found via live testing. Fixed in `supabase/migrations/0006_fix_outcome_labels_source_check.sql`; the "at least one source" invariant now lives solely in the `OutcomeLabel` pydantic validator.

### Resolved
- 2026-07-03 — OPEN_DECISIONS #3 (historical backfill): no, live-forward only. See `docs/OPEN_DECISIONS.md`.
