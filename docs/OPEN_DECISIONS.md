# AEOLUS — Open Decisions

From Spec §14. **Each must be resolved with the human before the affected module's ADR is approved.** Record resolution inline (date + decision) and update the affected directive.

## 1. Volume-participation range — affects TASK-006

Reframe of "opening range" to stay compliant with the no-clock-logic rule: the range formed by the first X% of the day's cumulative volume, not the first N minutes.

- Options: include as specified / drop entirely / redefine
- **Status:** RESOLVED (2026-07-03)
- **Resolution:** Include as specified. Range = first X% of cumulative session volume (X proposed and tuned in `directives/adr/TASK-006_order-flow-signals.md`). Unblocks TASK-006's third sub-signal.

## 2. DTE-graduated weighting — affects TASK-008

v1 spec = strict binary expiry/non-expiry config. Alternative: continuous DTE-based weighting (Monday behaves closer to Tuesday than Wednesday does).

- Options: keep binary for v1 (spec default) / graduated in v1
- **Status:** RESOLVED (2026-07-03)
- **Resolution:** Binary for v1, following the production-proven ARES pattern (`config_profiles.py`): two *complete* config tables (expiry-day vs non-expiry-day), each a full instance of the same schema — no partial overrides, no interpolation. Profile selected once per session and applied whole. Aeolus-specific mapping: expiry-day determination = TASK-007's DTE output. Per `directives/adr/TASK-007_context-signals.md`: no calendar is re-implemented in-repo — DTE is a plain subtraction against the expiry date Dhan's own `expiry_list` endpoint already resolves (holiday-shift aware by construction, not ARES's weekday shortcut); TASK-008's config loader selects the table from that DTE value (per its acceptance criteria) and the engine never reads a calendar itself. Rationale: no historical data exists to calibrate a DTE curve (decision #3 = live-forward only) — graduated weights in v1 would be guesses. Revisit graduation in v2 once TASK-012 outcome labels accumulate; config schema stays swappable-table-shaped so graduation is additive.

## 3. Historical backfill — affects TASK-001 scope, adds workstream if yes

Is backtesting against pre-launch dates a hard requirement? If yes → historical-data-sourcing workstream (NSE bhavcopy or paid vendor). If no → labeled dataset builds live from go-live forward.

- **Status:** RESOLVED (2026-07-03)
- **Resolution:** No. Live-forward only. Labeled dataset builds from go-live forward; no historical backfill workstream. TASK-001 scope unchanged.

## 4. Futures basis signal — affects TASK-002 (cheap add) + a signal module

Futures − spot and its drift through the session, as optional secondary positioning/sentiment signal. Direct futures feed already required, so marginal cost is low. Spec default: v2.

- **Status:** RESOLVED (2026-07-03)
- **Resolution:** Include now. TASK-002 exposes `futures_basis` (futures_ltp − spot_ltp) as a raw field on every ingestion snapshot. Session-drift interpretation scoped in `directives/adr/TASK-007_context-signals.md` (function 5, `futures_basis_drift`): reuses the confirm/diverge-vs-price-trend shape already established by CVD/PCR/GEX, human-approved 2026-07-03 with the "could be pure cost-of-carry noise, not real positioning signal" counter-argument flagged for later empirical check via `outcome_labels`.

## 5. Fly.io deployment & cron scheduling — affects TASK-013 deployment (not its code)

AEOLUS will deploy on Fly.io, scaled up/down by an external cron (same pattern as ARES — see Obsidian `Projects/Ares/09_Deployment.md`: cron-job.org calling the Fly Machines API directly, since GitHub Actions cron proved too delayed). Target window: machine up ~9:00 AM IST (gives a real pre-market buffer before the 9:15 open), machine down by ~3:31 PM IST. `Scheduler.run()` (TASK-013) is written to exit cleanly once its session is done specifically so this scale-to-0 pattern works without a separate "stop" trigger, mirroring ARES's `main.py`.

- **Status:** IN PROGRESS (2026-07-06) — deployment started; app boots on Fly, blocked only on Dhan data-API resubscription
- **Done (2026-07-05/06):**
  - [x] `fly.toml` — app `aeolus`, region `bom` (matching ARES), 1× shared-cpu-1x / 512MB, `[http_service]` removed (background worker)
  - [x] Fly app + region choice — `bom`, confirmed
  - [x] Dockerfile fixed: `pip install .` (non-editable) *after* `COPY src/` — the old editable-install-before-copy produced no `aeolus` module mapping (`ModuleNotFoundError` restart loop); `config/` now copied into the image too
  - [x] `NseCalendar` holidays path made install-safe: `AEOLUS_HOLIDAYS_PATH` env override → repo-relative → `cwd()/config` fallback (site-packages install broke the old file-relative walk)
  - [x] Secrets deployed via `fly secrets import` (Supabase URL/key + 3 Discord webhooks; market and ML webhooks intentionally the same channel)
  - [x] Scaled 2→1 machines (duplicate machines would double-post Discord)
- **TODO (remaining):**
  - [ ] Dhan data-API subscription renewal (human) — current 403 on scrip master is subscription expiry, not code; verify a full session run after resubscribing
  - [ ] cron-job.org (or equivalent) wired to `api.machines.dev` to scale 0→1 at session start (~9:00 AM IST up)
  - [ ] Confirm whether a symmetric scale-1→0 cron is still needed as a belt-and-suspenders stop, or whether `Scheduler.run()` exiting is sufficient alone (ARES kept both)
  - [ ] Re-run `scripts/fetch_nse_holidays.py` closer to 2026-11-08 to pick up muhurat-session hours once NSE publishes them (currently `null` in the live API response)

---

## ML Anomaly Module (from `files/AEOLUS_ML_ANOMALY_SPEC.md` §10 + one repo discrepancy)

## 6. Cleanup job does not exist — affects TASK-014 ⚠️ repo/spec discrepancy

ML Spec §3.2 and Build Prompt 1 assume an *existing* end-of-day cleanup job that trims `signal_snapshots`, and require modifying it + testing that `ml_*` survives it. The repo has **no cleanup job** — storage is an append-only log by design (`docs/DATA_MODEL.md`).

- **Status:** RESOLVED (2026-07-04)
- **Resolution:** **Build a real retention job now**, as part of TASK-014. Driver: `CYCLE_INTERVAL_SECONDS = 5.0` → ~4,500 cycles/session → ~20–25 MB/day across engine + ML tables ≈ 5+ GB/year on a Supabase instance shared with ARES — append-only forever is not viable. Policy (human-approved): trim `signal_snapshots` and `ml_anomaly_scores` older than **90 days**; prune `ml_model_registry` to the **last 30 versions per config**; NEVER touch `ml_feature_store`, `state_transitions`, `daily_outlook`, `outcome_labels` (denylist-driven, job refuses to operate on them). Runs LAST in the EOD sequence: `feature-store sync → retrain → cleanup`. Ships Build Prompt 1's run-cleanup-assert-protected-unchanged test. Steady-state DB ≈ 1.5–2 GB. This deliberately **supersedes ML Spec §6's blanket "all `ml_*` cleanup-protected"**: the training corpus (`ml_feature_store`) is absolutely protected; the advisory log (`ml_anomaly_scores`) is age-trimmed; the registry is count-pruned. Long-term supervised-phase data is preserved by `ml_feature_store` (features) + `outcome_labels` (labels), both permanent and small; `outcome_labels`' `ON DELETE SET NULL` FKs (migration 0005) were built for exactly this. Job lives engine-side in `src/aeolus/jobs/retention.py` (NOT in `aeolus.ml` — the ML module stays strictly read-only against engine tables); scheduler wiring in TASK-021 runs it after the ML EOD hook, and it runs even when ML is disabled.

## 7. Rolling window length — affects TASK-017

Default was 60 trading days once past warm-up (shorter = faster drift adaptation, longer = more regime diversity). Config value `window_days` in `MLTuning`.

- **Status:** RESOLVED (2026-07-04)
- **Resolution:** **30 trading days**, with retraining continuing daily (per #9) — human chose faster drift adaptation over the spec's 60-day default ("do 30 days and let it continue training"). Warm-up gates unchanged (≥10× n_features samples AND ≥15 distinct trading days per config), so the window grows from warm-up (~15 days) to the 30-day rolling cap, then rolls. `window_days = 30` in `MLTuning`, still a config value.

## 8. Flag percentile — affects TASK-017/018

Flag threshold = empirical percentile of training scores; clear threshold = lower hysteresis pair.

- **Status:** RESOLVED (2026-07-04)
- **Resolution:** Spec defaults confirmed — **flag = top 5%, clear = top 10%** of training-window scores, both recomputed on every retrain. `flag_pct = 0.05`, `clear_pct = 0.10` in `MLTuning`.

## 9. Retrain cadence — affects TASK-021

Daily end-of-day vs weekly.

- **Status:** RESOLVED (2026-07-04)
- **Resolution:** **Daily end-of-day** (spec default confirmed) — `sync → retrain → cleanup` in the scheduler's post-close sequence. Fit cost is seconds; no day-of-week logic needed anywhere.

## 10. Isolation-Forest + Mahalanobis ensemble for the decision — affects TASK-022 (v2)

v1: IF alone decides; Mahalanobis/z-score explains only. Two-voter decision mode is a v2 toggle (TASK-022), off by default.

- **Status:** DEFERRED to v2 — do not build TASK-022 until TASK-014..021 have run in production for ≥ one full rolling window.
