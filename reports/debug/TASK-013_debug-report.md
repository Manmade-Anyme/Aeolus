# Debug Report — TASK-013

**Date:** 2026-07-04
**Verdict:** ✅ CLEAN

## What was run
- `pytest tests/scheduler/ -q` — 4 `NseCalendar` tests + 7 `Scheduler` orchestration tests (fully faked collaborators + a controllable clock, no real sleeps thanks to a `time_module.sleep` monkeypatch).
- `pytest tests/engine/ -q` — 25 tests, confirming the `Engine.run_cycle` return-type amendment didn't break anything.
- `pytest -q` (full suite, 232 tests) — regression check.
- `ruff check` + `mypy` on `src/aeolus/scheduler/`, `src/aeolus/engine/`, `scripts/`.
- Live-fetched the real NSE FO-segment 2026 holiday calendar via `scripts/fetch_nse_holidays.py` and committed the result (`config/nse_holidays.json`) — not a fabricated/placeholder list.

## Design decisions changed mid-implementation (human-directed, not a bug)
The ADR's first draft assumed an always-on `run_forever()` server loop with idle-polling through nights/weekends. The human clarified the actual deployment model (Fly.io, scaled up/down by an external cron mirroring the sister ARES project — see Obsidian `Projects/Ares/09_Deployment.md`) partway through review, which changes the correct shape of this module: **`Scheduler.run()` is bounded to one trading session and returns when done**, so the process exits and the Fly machine scales to 0. This eliminated an entire category of state (day-rollover flag resets) since each process only ever handles one `session_date`. The ADR and this report both reflect the corrected design, not the original draft.

## Observed behavior
Full suite: `232 passed, 47 warnings` (pre-existing unrelated deprecation warnings). `ruff`/`mypy`: no issues on all touched/new files.

`scripts/fetch_nse_holidays.py` confirmed live against NSE's actual endpoint (`https://www.nseindia.com/api/holiday-master?type=trading`) — required a homepage request first to pick up session cookies (a bare API call 403s). Wrote 20 real 2026 FO-segment holidays; flagged one entry (2026-11-08, Diwali) where NSE's own API returns `null` for `morning_session`/`evening_session` — the muhurat special-session hours aren't published yet.

## Constraint audit
- [x] Schedule-gating ("is the market open") lives only in `Scheduler`/`NseCalendar` — other modules (`ingestion/feed_ws.py`, `ingestion/service.py`, `ingestion/feed_rest.py`, `engine/engine.py`) do call `datetime.now()`, but only for timestamping rows and elapsed-time staleness heartbeats (already-approved TASK-002/008 usages), never to branch *interpretation* on what time it currently is — the distinction constraint #2 actually draws, re-verified by grep, not "zero clock reads anywhere"
- [x] No per-signal veto — n/a, this module doesn't score anything
- [x] Deterministic — orchestration logic itself has no randomness; the one real external dependency (NSE holiday fetch) is explicitly an offline/periodic refresh step, not part of the hot path
- [x] Polarity n/a
- [x] `system_status` alerts fire independently of `market_state`'s hysteresis (verified in `test_system_status_change_posts_alert_but_not_on_first_cycle`), never gated by the same debounce
