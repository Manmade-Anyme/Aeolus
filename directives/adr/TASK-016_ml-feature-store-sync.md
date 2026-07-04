# Architecture Decision Record — TASK-016

**Directive:** `directives/TASK-016_ml-feature-store-sync.md`
**Status:** APPROVED — planning merged via [PR #17](https://github.com/dubeyshantanu2/Aeolus/pull/17) (commit `909de7d`), 2026-07-04
**Date:** 2026-07-04

## Problem

The training corpus must be a copy the ML module owns, populated continuously (live append) with an idempotent end-of-day catch-up, so that no future trimming of `signal_snapshots` and no mid-session crash can cost training history.

## Decision

`FeatureStore` class owning the supabase-py client for `ml_feature_store`, mirroring the constructor convention of the repo's other I/O classes (`supabase_url, supabase_key, *, client=None` injectable). Two write paths share one upsert primitive keyed on the `UNIQUE(source_snapshot_id)` constraint from migration 0009 — PostgREST `upsert(on_conflict="source_snapshot_id", ignore_duplicates=True)`, the same idempotency mechanism TASK-012 settled on after the 0007/0008 migration lesson (plain unique constraint, not partial indexes).

Live path `append(snapshot)`: extract via TASK-015, upsert one row. EOD path `sync_eod(session_date)`: select the session's `signal_snapshots` ids, anti-join against already-stored `source_snapshot_id`s, extract + upsert the gap. Because both paths funnel through the same extractor and upsert, a row written live and re-encountered at EOD is byte-identical and silently skipped — idempotency by construction, not by careful sequencing.

Standardized values: at append time no scaler may exist yet (warm-up), and the "current" scaler changes every retrain — so `standardized_values` is stored as computed *with the scaler active at write time* (or None), and is treated as informational. The trainer (TASK-017) always re-standardizes from `raw_values` with the window's own freshly fitted scaler; nothing downstream trusts stored standardized values for fitting. Alternative — re-writing standardized columns on every retrain — rejected: churn for no consumer.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/ml/store.py` | `FeatureStore`: `append`, `sync_eod`, `load_window`, `stored_snapshot_ids` |

## API Contracts

```python
class FeatureStore:
    def __init__(self, supabase_url: str, supabase_key: str, *, client: Client | None = None): ...

    def append(self, snapshot: SignalSnapshot, scaler: Scaler | None) -> bool:
        """Extract -> upsert. False (and no write) when extraction refuses
        (STALE/DISCONNECTED) or any feature is None. Never raises upward on
        duplicate — idempotent."""

    def sync_eod(self, session_date: date) -> int:
        """Copy the session's not-yet-persisted snapshot vectors. Returns rows
        written. Re-run => 0, no duplicates. MUST complete before retrain in
        the EOD sequence (enforced by TASK-021's hook ordering)."""

    def load_window(self, config_type: ConfigType, window_days: int) -> list[MLFeatureRow]:
        """Trainer's read path: most recent `window_days` DISTINCT session_dates
        for config_type, complete rows only (no None features)."""
```

## Performance / Failure Modes

Append adds one HTTP upsert per cycle — trivial next to the engine's existing per-cycle writes; wrapped by TASK-021 so a Supabase blip degrades to a missed row that `sync_eod` heals same day. Crash between live writes and EOD → healed. `sync_eod` reads `signal_snapshots` strictly read-only. Rows dropped for None-features are logged with counts so a silent upstream shape drift shows up in logs, not just as thin training data.

## Definition of Done

- [ ] Integration tests (real Supabase, self-cleaning): append→sync produces no duplicates; sync twice → second returns 0; STALE row never lands
- [ ] `load_window` windows by distinct session dates, filters config_type, excludes incomplete rows
- [ ] Constraint check: read-only vs `signal_snapshots`; no engine import of `aeolus.ml`; no clock logic (session_date is a parameter)
