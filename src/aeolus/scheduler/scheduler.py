"""Scheduler (TASK-013 ADR) -- the single entrypoint that runs AEOLUS. The
only module permitted to read wall-clock time for interpretation ("is the
market open") -- constraint #2 is absolute everywhere else.

Bounded to one trading session per process lifetime, by design: deployed on
Fly.io, scaled up/down by an external cron (see docs/OPEN_DECISIONS.md #5).
run() returns once the session is done so the process exits and the Fly
machine scales back to 0 -- there is deliberately no infinite idle-poll loop
across nights/weekends.
"""

from __future__ import annotations

import logging
import time as time_module
from datetime import date, datetime
from datetime import time as dt_time
from typing import Callable
from zoneinfo import ZoneInfo

from supabase import Client, create_client

from aeolus.engine.engine import Engine
from aeolus.jobs.backfill import OutcomeBackfillJob
from aeolus.ingestion.service import IngestionService
from aeolus.output.discord import DiscordDeliveryError, DiscordDispatcher
from aeolus.outlook.generator import OutlookGenerator
from aeolus.storage.models import DailyOutlook, SystemStatus

from .calendar import NseCalendar

IST = ZoneInfo("Asia/Kolkata")
CYCLE_INTERVAL_SECONDS = 5.0  # unconfirmed against Dhan's live rate-limit docs -- see ADR
PRE_OPEN_POLL_SECONDS = 15.0

logger = logging.getLogger("aeolus.scheduler")


def _now_ist() -> datetime:
    return datetime.now(IST)


class Scheduler:
    """Real collaborators are constructed by default; every one is
    injectable so orchestration logic (timing decisions, Discord dispatch,
    error containment) can be tested without live Dhan/Supabase/wall-clock
    dependencies -- those are genuinely impractical to exercise in a test
    (live market hours, a live websocket, hours of real elapsed time)."""

    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        market_webhook_url: str,
        status_webhook_url: str,
        cycle_interval_seconds: float = CYCLE_INTERVAL_SECONDS,
        *,
        client: Client | None = None,
        calendar: NseCalendar | None = None,
        ingestion: IngestionService | None = None,
        engine: Engine | None = None,
        outlook_generator: OutlookGenerator | None = None,
        backfill_job: OutcomeBackfillJob | None = None,
        discord: DiscordDispatcher | None = None,
        clock: Callable[[], datetime] = _now_ist,
    ) -> None:
        self._client: Client = client or create_client(supabase_url, supabase_key)
        self._calendar = calendar or NseCalendar()
        self._ingestion = ingestion or IngestionService(supabase_url, supabase_key)
        self._engine = engine or Engine(supabase_url, supabase_key)
        self._outlook_generator = outlook_generator or OutlookGenerator(supabase_url, supabase_key)
        self._backfill_job = backfill_job or OutcomeBackfillJob(supabase_url, supabase_key)
        self._discord = discord or DiscordDispatcher(market_webhook_url, status_webhook_url)
        self._cycle_interval_seconds = cycle_interval_seconds
        self._clock = clock

    def run(self) -> None:
        """Blocks for at most one trading session, then returns. Not a
        trading day -> returns immediately."""
        today = self._clock().date()
        if not self._calendar.is_trading_day(today):
            logger.info("session_date=%s is not a trading day, exiting", today)
            return

        hours = self._calendar.session_hours(today)
        assert hours is not None  # is_trading_day already confirmed this

        self._ingestion.start()
        try:
            outlook = self._run_pre_open(today, hours.open)
            self._run_live_loop(today, hours.close, outlook)
            self._engine.end_session()
            self._run_backfill(today)
        finally:
            self._ingestion.stop()

    def _daily_outlook_exists(self, session_date: date) -> bool:
        rows = (
            self._client.table(DailyOutlook.TABLE)
            .select("id")
            .eq("session_date", session_date.isoformat())
            .execute()
            .data
        )
        return bool(rows)

    def _run_pre_open(self, session_date: date, open_time: dt_time) -> DailyOutlook | None:
        while self._clock().time() < open_time and not self._daily_outlook_exists(session_date):
            time_module.sleep(PRE_OPEN_POLL_SECONDS)

        if self._daily_outlook_exists(session_date):
            logger.info("daily_outlook already exists for %s, not re-firing", session_date)
            return None

        outlook = self._outlook_generator.run(session_date, self._ingestion.latest())
        try:
            self._discord.post_outlook(outlook)
        except DiscordDeliveryError:
            logger.exception("failed to post pre-market outlook to Discord")
        return outlook

    def _run_live_loop(
        self, session_date: date, close_time: dt_time, outlook: DailyOutlook | None
    ) -> None:
        self._engine.start(session_date)
        assert self._ingestion.lot_size is not None  # set by ingestion.start()
        lot_size = self._ingestion.lot_size
        last_system_status: SystemStatus | None = None

        while self._clock().time() < close_time:
            try:
                snapshot = self._ingestion.latest()
                signal_snapshot, transition = self._engine.run_cycle(snapshot, lot_size)

                if transition is not None:
                    try:
                        self._discord.post_transition(transition, signal_snapshot, outlook)
                    except DiscordDeliveryError:
                        logger.exception("failed to post state transition to Discord")

                if last_system_status is not None and snapshot.system_status != last_system_status:
                    try:
                        self._discord.post_system_status(snapshot.system_status, last_system_status)
                    except DiscordDeliveryError:
                        logger.exception("failed to post system status alert to Discord")
                last_system_status = snapshot.system_status
            except Exception:
                logger.exception("cycle failed, continuing to next cycle")

            time_module.sleep(self._cycle_interval_seconds)

    def _run_backfill(self, session_date: date) -> None:
        try:
            self._backfill_job.run(session_date)
        except Exception:
            logger.exception("outcome backfill failed for %s", session_date)
