# QA Report — TASK-013

**Date:** 2026-07-04
**Verdict:** ✅ PASS

## Test summary
| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/scheduler/test_calendar.py` | 4 | 4 | 0 | Weekend exclusion, configured holiday, default hours, muhurat special-session override |
| `tests/scheduler/test_scheduler.py` | 7 | 7 | 0 | Non-trading-day short-circuit, outlook-fire/skip, live-loop cycling + transition dispatch, system-status alerting, Discord-failure containment, cycle-exception containment |
| `tests/engine/` (regression) | 25 | 25 | 0 | `run_cycle` return-type amendment |
| Full repo suite (`pytest -q`) | 232 | 232 | 0 | Regression check |

## Scenarios covered
- **`NseCalendar`:** weekends excluded regardless of the holiday file; a configured full-day holiday excluded; an ordinary weekday gets the default `(09:15, 15:30)`; a date that's both a holiday *and* has a special session (muhurat) resolves to the special hours, not `None` — the coexistence case the ADR specifically called out.
- **`Scheduler.run()`:** not a trading day → returns immediately without ever calling `ingestion.start()`; `daily_outlook` missing → fires the Outlook and posts it; `daily_outlook` already present → skips firing (restart-safety); the live loop runs exactly as many cycles as the clock sequence implies and stops at close; a returned `StateTransition` triggers exactly one `post_transition` call; a `system_status` change between cycle 1 and cycle 2 triggers exactly one `post_system_status` call, and — importantly — **no** alert fires on cycle 1 itself even though there's no prior status to compare against (verified explicitly, not just assumed); a `DiscordDeliveryError` raised mid-loop is caught and logged, the loop continues, `end_session()`/backfill still run afterward; a generic exception from `engine.run_cycle` is caught and logged, the loop continues to the next cycle instead of crashing the whole session.
- **`Engine.run_cycle` amendment:** all 5 existing call sites in `test_engine_integration.py` updated and passing, including a new explicit assertion that the hysteresis-flip test's returned transition matches the DB-queried one (previously only DB-side was checked).

## Edge cases exercised
- Restart-safety (`daily_outlook` already exists) — directly tested.
- Discord delivery failure not crashing the session — directly tested for both the transition and (implicitly, same code path) outlook/system-status call sites.
- A per-cycle exception not crashing the session — directly tested.

## Gaps / follow-ups (explicitly accepted, not silently glossed over)
- **No fully live end-to-end test of a real bounded trading session exists**, and one isn't practical to write here: it would need live Dhan market hours, a live websocket connection, and real elapsed wall-clock time across a multi-hour window. All `Scheduler` tests use fully faked collaborators (`ingestion`, `engine`, `outlook_generator`, `backfill_job`, `discord`, the Supabase client, and the clock are all injectable per the ADR) plus a monkeypatched `time.sleep`. This is a deliberate, documented trade-off, not an oversight — genuine live verification will happen once this is actually deployed (see `docs/OPEN_DECISIONS.md` #5).
- **`CYCLE_INTERVAL_SECONDS` (5s default) is unconfirmed against Dhan's live rate-limit docs** — flagged in the ADR, not resolved here.
- **Muhurat-session hours for 2026-11-08 are not yet in `config/nse_holidays.json`** because NSE's own API doesn't have them published yet (`null`/`null` in the live response) — `scripts/fetch_nse_holidays.py` needs a re-run closer to that date.
- **Fly.io deployment (`fly.toml`, cron scheduling) is out of scope for this task entirely**, by explicit human direction — tracked in `docs/OPEN_DECISIONS.md` #5, not part of this report's pass/fail scope.
