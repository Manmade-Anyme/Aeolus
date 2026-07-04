# QA Report — TASK-016

**Date:** 2026-07-04
**Verdict:** ⚠️ CONDITIONAL PASS — code + tests complete; live-Supabase run pending human DDL apply (same gate as TASK-014)

## Test summary
| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/ml/test_store_integration.py` (live) | 4 | 0 | 4 (blocked) | append idempotency on replay, STALE-row exclusion, `sync_eod` copy + second-pass no-op, `load_window` config_type filter + distinct-date windowing |
| `tests/ml/` regression (no DB) | 19 | 19 | 0 | TASK-014 models + TASK-015 features, unaffected |

## Scenarios covered
- **Append + replay:** builds one complete `SignalSnapshot`, appends it twice; asserts both calls return `True` (extraction succeeds both times) and exactly one `ml_feature_store` row exists afterward — the `UNIQUE(source_snapshot_id)` + `ignore_duplicates=True` upsert absorbing the replay.
- **STALE exclusion:** a `STALE` snapshot's `append()` returns `False` and no row is written — verified by querying `ml_feature_store` directly, not just trusting the return value.
- **`sync_eod`:** seeds one OK and one STALE `signal_snapshots` row for the same session; `sync_eod` returns `1` (only the OK row extracts), then returns `0` on a second call with nothing left to copy.
- **`load_window`:** seeds `ml_feature_store` rows across 3 distinct `EXPIRY` session dates plus 1 `NON_EXPIRY` row on the latest date; `load_window("EXPIRY", 2)` returns exactly the 2 most-recent-date `EXPIRY` rows, excluding both the oldest `EXPIRY` date and the `NON_EXPIRY` row.

## Edge cases exercised
- Replay/idempotency at both the `sync_eod` anti-join layer and the underlying upsert's conflict-handling — the ADR explicitly wants both, not just one.
- "Complete rows only" for `load_window` is structurally guaranteed rather than separately tested: `append`/`sync_eod` never write a row with a `None` feature, so there is nothing incomplete in the table to filter — re-asserted here rather than silently assumed.

## Gaps / follow-ups
- **Same migration-apply blocker as TASK-014**: `supabase/migrations/0009..0011_*.sql` need to be run via the Supabase Dashboard SQL Editor before this task's 4 live tests (and TASK-014's 3) can actually execute. Recommend applying all six pending migrations (0009-0011, none since added) in one sitting, then re-running `pytest tests/ml/ tests/jobs/test_retention_integration.py -q` to confirm both tasks live end-to-end.
- No test exercises `append(snapshot, scaler=<real Scaler>)` writing non-null `standardized_values` — deferred naturally to TASK-017, which is the first task that actually produces a fitted `Scaler`; `standardize()` itself is already unit-tested in TASK-015.
