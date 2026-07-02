# Changelog

All notable changes to AEOLUS. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- 2026-07-03 — Project scaffold: folder structure, pipeline docs (`docs/`), PM directives TASK-001..013, ADR/report templates, project `CLAUDE.md`. No code yet.
- 2026-07-03 — TASK-001 Supabase schema: 5 migration files (`supabase/migrations/`) for `signal_snapshots`, `state_transitions`, `daily_outlook`, `outcome_labels` + enum types; typed pydantic models (`src/aeolus/storage/models.py`); ADR at `directives/adr/TASK-001_supabase-schema.md`. `system_status`/`market_state` enforced as structurally distinct Postgres enum types. Model-layer tests pass (16/16); DB-level DDL tests written, pending `TEST_DATABASE_URL`.

### Resolved
- 2026-07-03 — OPEN_DECISIONS #3 (historical backfill): no, live-forward only. See `docs/OPEN_DECISIONS.md`.
