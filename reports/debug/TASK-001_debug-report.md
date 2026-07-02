# Debug Report — TASK-001

**Date:** 2026-07-03
**Verdict:** ✅ CLEAN

## What was run

- `python3 -m venv .venv && pip install -e ".[dev]"`
- `python -m pytest tests/ -v` (full suite, `src/aeolus` package)
- Manual `python -c "import aeolus.storage.models"` sanity checks during setup

## Observed behavior

Initial setup hit an environment issue unrelated to TASK-001 code: `import aeolus` failed with `ModuleNotFoundError` under pytest despite a successful editable install. Root cause: hatchling's editable-install `.pth` file (`_editable_impl_aeolus.pth`) was created with the macOS `UF_HIDDEN` filesystem flag set. CPython 3.14's `site.addpackage()` explicitly skips `.pth` files with that flag (`st_flags & stat.UF_HIDDEN`), so the `src/` path was silently never added to `sys.path`. Fixed with `chflags nohidden` on the file. This is a local-environment artifact (how this particular venv/pip run created the file), not a code defect — noted here in case it recurs on re-setup.

Also found `src/aeolus/__init__.py` was missing (only `src/aeolus/storage/__init__.py` existed) — added, since the package root needs it for the editable install to resolve `aeolus.storage.models` correctly.

All other behavior matched expectations from the ADR.

## Issues

| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| 1 | Low (env, not code) | Editable-install `.pth` created with macOS hidden flag, breaks import under pytest | n/a (venv artifact) | Fixed (`chflags nohidden`) — recurred a second time after `pip install -e` was re-run to pick up dependency changes; same fix re-applied. Confirmed recurring on every editable reinstall on this machine. |
| 2 | Low | `src/aeolus/__init__.py` missing | `src/aeolus/__init__.py` | Fixed |
| 3 | Medium (real bug) | `outcome_labels_has_source` CHECK constraint (0005) conflicts with `ON DELETE SET NULL` FK on the same table: deleting a referenced `signal_snapshots` row with only `snapshot_id` set on the label causes Postgres to SET NULL, which then trips the CHECK (both sources null) and rejects the whole DELETE | `supabase/migrations/0005_outcome_labels.sql:18-20` | Fixed — `0006_fix_outcome_labels_source_check.sql` drops the CHECK; invariant now enforced only by `OutcomeLabel` pydantic validator. Applied live 2026-07-03. |

## Second debug pass — live Supabase verification (2026-07-03)

## What was run

- `.env` confirmed to hold `SUPABASE_URL`/`SUPABASE_KEY` (anon key, JWT `role: anon`) — same shared Supabase project used by the Ares project (confirmed via `PGRST205` error hinting at Ares's `gex_snapshots` table when `signal_snapshots` didn't exist yet)
- User applied `supabase/migrations/0001..0005` + RLS-disable statements via the Supabase Dashboard SQL Editor (anon key cannot run DDL over REST — no `exec_sql` RPC exists on this project, confirmed by direct attempt)
- `python -m pytest tests/storage/test_supabase_integration.py -v` — live client-based tests, found the CHECK/FK conflict above
- Migration `0006` applied the same way; suite re-run, full pass

## Observed behavior

`test_outcome_label_snapshot_delete_sets_null_not_cascade` failed on first live run with `APIError 23514: new row for relation "outcome_labels" violates check constraint "outcome_labels_has_source"` — raised on the `DELETE FROM signal_snapshots` call, not on any `outcome_labels` write. This is the model-only test suite's blind spot: `tests/storage/test_models.py` validates pydantic construction only, never exercises DB-side cascade/trigger behavior, so this conflict was invisible until tested against real Postgres. After `0006` was applied, the same test passed, and a follow-up test (`test_outcome_label_no_source_is_db_level_permitted_but_app_level_blocked`) was added confirming the DB now permits a sourceless row (by design — enforcement moved to the pydantic layer) since TASK-012 is the only writer of this table.

## Constraint audit

- [x] No per-signal veto present — n/a, this task is schema/DDL only, no scoring logic
- [x] No clock-time branching in signal logic — n/a, no signal logic in this task
- [x] Reason strings deterministic — n/a, schema stores `reason`/`reasons` as plain text/jsonb, generation happens in TASK-010, not here
- [x] Polarity check: GO favors option buying — n/a, enums only, no interpretation logic
- [x] `system_status` never feeds `market_state` — verified structurally: separate Postgres enum types (`0001_enums.sql`), separate columns on `signal_snapshots`, separate `Literal` types in `SignalSnapshot` pydantic model. Cross-assignment is a type error at both the DB and model layer.
