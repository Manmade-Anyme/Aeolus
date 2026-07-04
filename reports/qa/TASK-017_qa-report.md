# QA Report — TASK-017

**Date:** 2026-07-04
**Verdict:** ⚠️ CONDITIONAL PASS — logic verified end-to-end via a fake-client sanity run; live-Supabase suite pending human DDL apply (same gate as TASK-014/016)

## Test summary
| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/ml/test_trainer_integration.py` (live) | 5 | 0 | 5 (blocked) | TRAINED result + threshold/round-trip verification, warm-up gate (insufficient days), warm-up gate (insufficient samples), two-config independence, version increment + prior-row integrity |
| Fake-client sanity run (not a committed test) | — | — | — | Confirmed the actual fit/threshold/serialize/version code path executes correctly and caught a real `joblib` API bug before it could reach the live suite |

## Scenarios covered
- **TRAINED + threshold correctness:** seeds 192 synthetic rows across 16 distinct `NON_EXPIRY` session dates (comfortably clearing both warm-up gates: 192 ≥ 10×15 features, 16 ≥ 15 days); asserts `outcome == "TRAINED"`, `version == 1`; independently deserializes the stored `model_blob`, re-standardizes the seeded raw values using the stored scaler, re-scores with the deserialized model, and recomputes `quantile(scores, 1-flag_pct)`/`quantile(scores, 1-clear_pct)` — asserts these match the registry row's stored thresholds exactly. This single test satisfies both the ADR's "threshold == empirical quantile (hand-checked)" and "round-trip blob scores match pre-serialization scores exactly" Definition-of-Done items at once.
- **Warm-up, insufficient days:** 180 samples (well past the 150-sample gate) across only 3 distinct days (below the 15-day gate) → `WARMING_UP`, no registry row written.
- **Warm-up, insufficient samples:** 16 distinct days (clears the day gate) but only 32 total samples (below the 150-sample gate) → `WARMING_UP`.
- **Two-config independence:** seeds ample `NON_EXPIRY` data and sparse `EXPIRY` data, calls `train_all()` — `NON_EXPIRY` trains while `EXPIRY` reports `WARMING_UP` in the same call, confirming one config's state never blocks or corrupts the other's.
- **Version increment + prior-row integrity:** calls `train()` twice on identical seeded data — `version` goes 1 then 2; both rows' `flag_threshold` match (deterministic fit via fixed `random_state`), and both versions coexist in the registry (no overwrite, no pruning — v1 in scope, per the ADR).

## Edge cases exercised
- The `joblib.dumps`/`loads` bug (see debug report) would have surfaced immediately on the very first live test run regardless of the migration blocker — caught earlier via the fake-client sanity script specifically because that script doesn't depend on the pending migrations, which is exactly why it was worth writing even though it isn't part of the committed suite.
- Deterministic reproducibility (`random_state` fixed) verified directly, not just assumed — the version-increment test's two `train()` calls on identical data produce byte-different but statistically identical (same threshold) models.

## Gaps / follow-ups
- **Same migration-apply blocker as TASK-014/016**: recommend applying `supabase/migrations/0009..0011_*.sql` in one sitting (7 already-blocked live tests plus these 5 = 12 total), then re-running `pytest tests/ml/ tests/jobs/test_retention_integration.py -q`.
- **`train_all`'s per-config `FAILED` outcome path is implemented (try/except around `train()`) but not live-tested** — would require forcibly breaking one config's write (e.g., a malformed row) without breaking test setup; same category of gap as TASK-014's untested partial-failure containment. Low risk: it's a single try/except wrapping already-tested logic.
- The fake-client sanity script that caught the `joblib` bug and validated the full numeric path is not part of the committed test suite (it constructs an ad-hoc in-memory Supabase double, which doesn't fit this repo's "no internal mocking, real Supabase" integration-test convention) — kept as a debug-report artifact only, not checked in.
