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
| 1 | Low (env, not code) | Editable-install `.pth` created with macOS hidden flag, breaks import under pytest | n/a (venv artifact) | Fixed (`chflags nohidden`) — will recur on fresh `.venv` rebuild; re-apply if `import aeolus` fails after a clean install |
| 2 | Low | `src/aeolus/__init__.py` missing | `src/aeolus/__init__.py` | Fixed |

## Constraint audit

- [x] No per-signal veto present — n/a, this task is schema/DDL only, no scoring logic
- [x] No clock-time branching in signal logic — n/a, no signal logic in this task
- [x] Reason strings deterministic — n/a, schema stores `reason`/`reasons` as plain text/jsonb, generation happens in TASK-010, not here
- [x] Polarity check: GO favors option buying — n/a, enums only, no interpretation logic
- [x] `system_status` never feeds `market_state` — verified structurally: separate Postgres enum types (`0001_enums.sql`), separate columns on `signal_snapshots`, separate `Literal` types in `SignalSnapshot` pydantic model. Cross-assignment is a type error at both the DB and model layer.
