# TASK-001 Supabase Schema & Migrations

**Goal:** Four Postgres tables (`signal_snapshots`, `state_transitions`, `daily_outlook`, `outcome_labels`) exist via migration files, matching Spec §9 / `docs/DATA_MODEL.md`.

**Acceptance Criteria:**
- [ ] Migration files create all four tables
- [ ] Indexes on timestamp + config_type on `signal_snapshots`
- [ ] `system_status` enum (`OK`/`STALE`/`DISCONNECTED`) separate from `market_state` enum (`NO_GO`/`PREPARE`/`GO`)
- [ ] `signal_snapshots` stores raw per-category readings + reference bands — composite must be recomputable retroactively if weights change (not just final score)
- [ ] `daily_outlook` has prior-day trend-exhaustion flag as its own field
- [ ] Migrations apply cleanly to a fresh Supabase project

**Inputs:** Spec §9, `docs/DATA_MODEL.md`, resolution of OPEN_DECISIONS #3 (historical backfill scope).

**Output:** Migration files, enum definitions, schema doc update in `docs/DATA_MODEL.md` (conceptual → concrete DDL reference).

**Edge Cases:** re-running migrations (idempotency); timezone handling (IST session times stored unambiguously, use timestamptz).

**Depends on:** none (build first).

**Global constraints:** see `docs/CONSTRAINTS.md` — esp. system_status ≠ market_state.

**Status:** DRAFT
