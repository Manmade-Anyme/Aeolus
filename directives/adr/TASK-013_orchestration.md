# Architecture Decision Record — TASK-013

**Directive:** `directives/TASK-013_orchestration.md`
**Status:** APPROVED (2026-07-04)
**Date:** 2026-07-03

## Problem

The single entrypoint that runs AEOLUS: wires `IngestionService` (TASK-002), `Engine` (TASK-008), `OutlookGenerator` (TASK-009), `DiscordDispatcher` (TASK-011), and `OutcomeBackfillJob` (TASK-012) into one continuous process spanning the full NSE session, plus the single pre-open and post-close triggers. This is the **only** module allowed to read wall-clock time for interpretation purposes ("is the market open") — constraint #2 is otherwise absolute everywhere else in the codebase.

**Deployment model (human-directed, changes the shape of this ADR from its first draft):** AEOLUS runs on Fly.io, scaled up/down by an external cron (same pattern as the sister project ARES on the same Fly account — see Obsidian `Projects/Ares/09_Deployment.md`: cron-job.org calls the Fly Machines API directly to scale the app to 1 machine at session start and back to 0 at session end, since GitHub Actions cron proved too delayed for this). **The process itself is bounded to one trading session, not an infinite server loop** — it starts when Fly scales it up (~9:00 AM IST, giving a real 15-minute pre-market window before the 9:15 open), runs the day, and **exits** once it's done (end-of-session cleanup + backfill) so the Fly machine scales back to 0 and stops billing. This is a significant simplification from an always-on idle-polling design: no multi-day state to track in-process, no need to idle-poll through nights/weekends — the external cron already decided *when* to run today; this process just needs to decide, once at startup, *whether* today is actually a trading day (the cron may well fire on a holiday it doesn't know about) and then run one bounded session. **Setting up the actual Fly.io app/fly.toml/cron scheduling is explicitly out of scope for this task** — deferred to a follow-up discussion once the system runs correctly end-to-end (tracked in `docs/OPEN_DECISIONS.md` #5).

**Three real gaps surfaced while writing this ADR:**
1. **No cycle interval is defined anywhere.** TASK-002's own ADR explicitly deferred this: *"Call cadence is entirely the caller's responsibility (the scheduler, TASK-013). This module doesn't guess a safe interval."* `IngestionService.latest()` re-polls Dhan's REST option-chain endpoint on every call — the scheduler's loop interval **is** the Dhan API call rate. Needs a real number, confirmed against Dhan's live rate-limit docs before production use. **Still flagged, unresolved** — orthogonal to the deployment-model question above.
2. **No NSE trading-holiday/special-session calendar exists anywhere in this codebase.** The Dhan SDK has no holiday-calendar endpoint (checked — `dhanhq` exposes nothing holiday/calendar-related). `context_signals.dte()` only knows about *expiry*-date holiday-shifting (via Dhan's own `expiry_list`), not "is today a trading day at all" or "is today a shortened/muhurat session." **Resolved**: NSE publishes exactly this via a public (if undocumented) JSON endpoint — confirmed live: `GET https://www.nseindia.com/api/holiday-master?type=trading` (needs a browser-like `User-Agent` and a prior request to the homepage to pick up session cookies; a bare request 403s). Fetched the real FO-segment 2026 calendar this way — see "NSE trading calendar" below. One entry (`2026-11-08`, Diwali/muhurat) has `morning_session`/`evening_session` both `null` in the live response today — NSE hasn't published the special muhurat-session hours yet (typically announced ~1 week ahead); flagged for a re-fetch closer to that date.
3. **`Engine.run_cycle` doesn't expose whether a transition fired.** It writes `StateTransition` internally when `flipped` is true, but returns only `SignalSnapshot`. The scheduler needs to know, per cycle, whether to call `DiscordDispatcher.post_transition(...)` — without this, it would need a redundant `state_transitions` query every cycle. Small additive amendment to TASK-008's return type.

## Decision

### 1. Cycle interval — flagged number, not silently guessed

**Default: 5 seconds**, as a module constant `CYCLE_INTERVAL_SECONDS` in the new scheduler module — chosen above the commonly-cited Dhan v2 option-chain rate limit (reported elsewhere as ~1 request per 3 seconds per instrument), with headroom. **This number is explicitly unconfirmed against Dhan's actual current rate-limit documentation** — same hedge TASK-002's ADR already used for the same reason. Flagging for you to verify against the live Dhan dashboard/docs before this runs against a real account; trivially adjustable (one constant) once confirmed.

### 2. NSE trading calendar — fetched from NSE's own live endpoint, not fabricated

New `src/aeolus/scheduler/calendar.py`:
```python
class NseCalendar:
    def is_trading_day(self, d: date) -> bool: ...      # False for weekends + configured holidays
    def session_hours(self, d: date) -> SessionHours | None:  # None if not a trading day
```
Weekends (Sat/Sun) are hardcoded as non-trading — that's a calendar fact, not a judgment call.

**Data source: `scripts/fetch_nse_holidays.py`**, a standalone refresh script (not run on the hot path) hitting NSE's public holiday-master endpoint (`GET https://www.nseindia.com/api/holiday-master?type=trading`, `FO` segment — confirmed live during this ADR) and writing `config/nse_holidays.json`. **Deliberately not fetched live at every session startup** — this is an undocumented public endpoint (needs cookie warm-up via a homepage request first, a bare API call 403s), and a scraping dependency on the critical "should I even start trading today" path is a worse failure mode than a periodically-refreshed static file. Run it once now (real 2026 FO-segment data committed), and re-run it whenever NSE publishes an updated circular (at minimum once a year, and again ~1 week before Diwali once muhurat-session hours are announced — see gap #2 above).

Format:
```json
{
  "holidays": ["2026-01-26", "..."],
  "special_sessions": {"2026-11-08": {"open": "18:00", "close": "19:00"}}
}
```
`session_hours(d)` checks `special_sessions` first (a date can be both a full holiday *and* have a muhurat special session — Diwali is exactly this case), then `holidays` (→ `None`), then weekend (→ `None`), else the default `(09:15, 15:30)`.

### 3. `Engine.run_cycle` return-type amendment (TASK-008 boundary, additive)

`run_cycle(...) -> SignalSnapshot` becomes `run_cycle(...) -> tuple[SignalSnapshot, StateTransition | None]` — the transition it already builds internally when `flipped`, or `None` otherwise. Five existing call sites in `tests/engine/test_engine_integration.py` need updating to unpack the tuple; no other module calls `run_cycle`. This avoids the scheduler needing a second `state_transitions` query every cycle just to detect what `Engine` already knows.

### 4. The main loop — bounded to one session, not an infinite server

New `src/aeolus/scheduler/scheduler.py::Scheduler`, one blocking `run()` entrypoint (the directive's "single entrypoint to run AEOLUS") that **returns when the session is done**, so the process exits and Fly scales back to 0. All wall-clock reads use IST (`zoneinfo.ZoneInfo("Asia/Kolkata")`, stdlib, no new dependency) — the only module in the codebase permitted to do this. No day-rollover state tracking is needed — Fly's external cron starts a fresh process each trading day, so this process only ever handles a single `session_date` for its entire lifetime.

```
def run():
    today = now(IST).date()
    if not calendar.is_trading_day(today):
        log "not a trading day, exiting"; return

    open_t, close_t = calendar.session_hours(today)
    ingestion.start()
    try:
        # pre-open window (process typically starts ~9:00, open_t is 9:15 -- this
        # short wait *is* the directive's "single pre-open trigger" window)
        while now(IST).time() < open_t and not daily_outlook row exists for today:
            sleep(PRE_OPEN_POLL_SECONDS)
        if not daily_outlook row exists for today:
            outlook = outlook_generator.run(today, ingestion.latest())
            discord.post_outlook(outlook)  # caught, logged on DiscordDeliveryError

        # live loop
        engine.start(today)
        last_system_status = None
        while now(IST).time() < close_t:
            snapshot = ingestion.latest()
            signal_snapshot, transition = engine.run_cycle(snapshot, ingestion.lot_size)
            if transition is not None:
                discord.post_transition(transition, signal_snapshot, outlook)  # caught, logged
            if snapshot.system_status != last_system_status and last_system_status is not None:
                discord.post_system_status(snapshot.system_status, last_system_status)  # caught, logged
            last_system_status = snapshot.system_status
            sleep(CYCLE_INTERVAL_SECONDS)

        engine.end_session()
        backfill_job.run(today)
    finally:
        ingestion.stop()
```

**Why checking `daily_outlook` existence rather than a pure in-memory flag for the outlook trigger:** covers the "deploy during market hours" edge case for free — if Fly starts the machine late (after `open_t`) for any reason, the wait loop's time condition is already false, so it falls straight through to the existence check and fires immediately if missing (better a late Outlook than none), with zero extra branching versus the normal on-time case.

**`engine.end_session()` at `close_t` (3:31pm IST default, per `engine.py`'s own existing docstring — not a new number, reusing what TASK-008 already documented as its expected trigger time).**

**Restart mid-session (the process crashes and Fly/a supervisor restarts it before `close_t`):** `Engine.start(session_date)` already reloads all trailing histories, hysteresis counters, and session-scoped state from Supabase (TASK-008 ADR) — a fresh `Scheduler.run()` invocation just calls `start()` again and resumes cycling; no bespoke recovery logic needed here. If it restarts after the pre-open wait loop's window, the `daily_outlook`-existence check (already computed once at the top) means it skips straight to the live loop without re-running the Outlook. A per-cycle exception (e.g. a transient Supabase blip) is caught, logged, and the loop continues to the next cycle rather than crashing the whole process; a hard failure in `ingestion.start()` or the initial `engine.start()` is allowed to propagate — there's no meaningful degraded mode to run a trading-hours loop in without those.

**System-status alerts fire immediately on change (no hysteresis)** — distinct from `market_state`'s mandatory debounce (Spec §7 applies only to state *transitions*, not feed-health alerts); tracked via a simple "did `system_status` differ from last cycle's" check.

**Discord failures never crash the loop** — `DiscordDeliveryError` (TASK-011) is caught and logged per call site; a failed post is a logged miss, not a scheduler crash, per TASK-011 ADR's own stated expectation of what its caller should do.

### Component Boundaries

| File | Responsibility |
|------|---|
| `scripts/fetch_nse_holidays.py` | One-off/periodic refresh script — fetches NSE's live holiday-master endpoint, writes `config/nse_holidays.json` |
| `config/nse_holidays.json` | Real fetched 2026 FO-segment holiday data (committed) — re-run the fetch script for future years/muhurat-hour updates |
| `src/aeolus/scheduler/calendar.py` | `NseCalendar` — trading-day/session-hours lookup from the data file |
| `src/aeolus/scheduler/scheduler.py` | `Scheduler.run()` — the bounded single-session loop described above |
| `src/aeolus/engine/engine.py` | `run_cycle` return-type amendment (additive) |
| `scripts/run_aeolus.py` | Thin process entrypoint reading env vars, constructing `Scheduler`, calling `run()`, then exiting — what Fly's cron-scaled machine actually invokes |

## API Contracts

```python
class NseCalendar:
    def __init__(self, holidays_path: str = "config/nse_holidays.json") -> None: ...
    def is_trading_day(self, d: date) -> bool: ...
    def session_hours(self, d: date) -> SessionHours | None: ...  # None if not a trading day


class Scheduler:
    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        market_webhook_url: str,
        status_webhook_url: str,
        cycle_interval_seconds: float = CYCLE_INTERVAL_SECONDS,
    ) -> None: ...

    def run(self) -> None:
        """Blocks for at most one trading session, then returns (process exits,
        Fly scales to 0). Not a trading day -> returns immediately. KeyboardInterrupt ->
        clean shutdown. Per-cycle exceptions logged and skipped, not raised."""


# Engine amendment (TASK-008 boundary)
def run_cycle(self, snapshot: IngestionSnapshot, lot_size: int) -> tuple[SignalSnapshot, StateTransition | None]: ...
```

## Performance / Failure Modes

- Dhan REST rate limit: governed entirely by `CYCLE_INTERVAL_SECONDS` — the one number in this whole system that directly controls external API call rate. Flagged above for confirmation.
- A Supabase outage mid-cycle: caught, logged, retried next cycle — `system_status` (already independent of `market_state`) will reflect feed degradation from `IngestionService`'s own staleness tracking regardless of the scheduler's own Supabase-write failures; this scheduler doesn't add a second staleness concept on top.
- Clock skew: the scheduler trusts the host's system clock for IST wall-time reads — no NTP-drift handling is in scope; flagged as an accepted limitation, not solved here.
- Muhurat/special sessions: handled by the same `session_hours()` lookup as any other day — no special-casing in the loop itself, just a different `(open, close)` pair for that date.

## Definition of Done

- [ ] Integration-style tests against `Scheduler`'s behavior (constructed with fake/injectable `IngestionService`/`Engine`/etc. or real Supabase for state — real HTTP dependencies mocked at the transport level like TASK-011's tests, not internal method mocking)
- [ ] `NseCalendar` unit tests: weekend exclusion, configured holiday exclusion, special-session hours override, default hours otherwise
- [ ] Restart-safety test: outlook already exists for today → not re-fired; backfill flag independent of DB state (idempotent regardless)
- [ ] `Engine.run_cycle` amendment: existing 5 test call sites updated, transition returned exactly when a flip occurs and `None` otherwise
- [ ] Constraint check: schedule-gating lives only here (verified no other module branches on time), no per-signal veto (n/a), deterministic (scheduling logic itself has no randomness), polarity n/a
