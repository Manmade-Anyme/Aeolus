# TASK-016 ML Feature-Store Sync

**Goal:** Persist every scored cycle's feature vector into `ml_feature_store` — live append per cycle plus an idempotent end-of-day catch-up — so the training corpus survives independently of `signal_snapshots`.

**Acceptance Criteria:**
- [ ] Live path: `append(...)` writes one `ml_feature_store` row per scored cycle (raw + standardized values, source snapshot id, config_type, timestamp)
- [ ] EOD backstop: `sync_eod(session_date)` copies any snapshot rows from the session not yet in the store
- [ ] Idempotent — re-running `sync_eod` never duplicates rows (unique constraint on source snapshot id, upsert against it)
- [ ] Skips `STALE`/`DISCONNECTED` snapshots (via TASK-015's extraction refusal)
- [ ] Runs strictly BEFORE retrain and cleanup in the EOD sequence `sync → retrain → cleanup` (ordering enforced by TASK-021; cleanup = TASK-014's RetentionJob)

**Inputs:** ML Spec §3.2, §7; Build Prompt 3.

**Output:** `src/aeolus/ml/store.py`.

**Edge Cases:** partial writes mid-session then crash (EOD sync heals); snapshot rows whose extraction returns None (excluded, not an error); re-running sync for an already-synced session.

**Depends on:** TASK-014, TASK-015.

**Global constraints:** see `docs/CONSTRAINTS.md` (incl. ML overlay constraints). Read-only against `signal_snapshots`.

**Status:** APPROVED — planning merged via [PR #17](https://github.com/dubeyshantanu2/Aeolus/pull/17) (commit `909de7d`), 2026-07-04
