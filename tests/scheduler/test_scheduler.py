"""Scheduler orchestration tests (TASK-013 ADR). Every collaborator
(ingestion, engine, outlook generator, backfill job, discord, the Supabase
client, the clock) is a lightweight fake -- exercising the *orchestration
logic* (timing decisions, Discord dispatch, error containment) in isolation
from live Dhan/Supabase/wall-clock dependencies, which are genuinely
impractical to exercise in a test (see ADR / QA report for the accepted gap
this leaves: no fully live end-to-end test of a real bounded session).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest

from aeolus.ingestion.models import IngestionSnapshot
from aeolus.output.discord import DiscordDeliveryError
from aeolus.scheduler.calendar import SessionHours
from aeolus.scheduler.scheduler import Scheduler
from aeolus.storage.models import DailyOutlook, StateTransition


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("aeolus.scheduler.scheduler.time_module.sleep", lambda _seconds: None)


class FakeCalendar:
    def __init__(self, trading: bool, hours: SessionHours | None = None):
        self._trading = trading
        self._hours = hours or SessionHours(open=time(9, 15), close=time(15, 30))

    def is_trading_day(self, d: date) -> bool:
        return self._trading

    def session_hours(self, d: date) -> SessionHours | None:
        return self._hours if self._trading else None


class FakeClock:
    def __init__(self, values: list[datetime]):
        self._values = values
        self._index = 0

    def __call__(self) -> datetime:
        value = self._values[min(self._index, len(self._values) - 1)]
        self._index += 1
        return value


@dataclass
class FakeIngestion:
    lot_size: int = 75
    snapshots: list[IngestionSnapshot] = field(default_factory=list)
    started: bool = False
    stopped: bool = False
    _index: int = 0

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def latest(self) -> IngestionSnapshot:
        snapshot = self.snapshots[min(self._index, len(self.snapshots) - 1)]
        self._index += 1
        return snapshot


class FakeEngine:
    def __init__(self, results: list[tuple[object, object]]):
        self._results = results
        self._index = 0
        self.started_with: date | None = None
        self.ended = False
        self.call_count = 0

    def start(self, session_date: date) -> None:
        self.started_with = session_date

    def run_cycle(self, snapshot, lot_size):
        self.call_count += 1
        result = self._results[min(self._index, len(self._results) - 1)]
        self._index += 1
        if isinstance(result, Exception):
            raise result
        return result

    def end_session(self) -> None:
        self.ended = True


class FakeOutlookGenerator:
    def __init__(self, outlook: DailyOutlook):
        self._outlook = outlook
        self.calls: list[tuple[date, IngestionSnapshot]] = []

    def run(self, session_date: date, snapshot: IngestionSnapshot) -> DailyOutlook:
        self.calls.append((session_date, snapshot))
        return self._outlook


class FakeBackfillJob:
    def __init__(self):
        self.calls: list[date] = []

    def run(self, session_date: date) -> None:
        self.calls.append(session_date)


class FakeDiscord:
    def __init__(self, raise_on: frozenset[str] = frozenset()):
        self.outlook_calls: list[DailyOutlook] = []
        self.transition_calls: list[tuple] = []
        self.system_status_calls: list[tuple] = []
        self._raise_on = raise_on

    def post_outlook(self, outlook: DailyOutlook) -> None:
        self.outlook_calls.append(outlook)
        if "outlook" in self._raise_on:
            raise DiscordDeliveryError("boom")

    def post_transition(self, transition, snapshot, outlook) -> None:
        self.transition_calls.append((transition, snapshot, outlook))
        if "transition" in self._raise_on:
            raise DiscordDeliveryError("boom")

    def post_system_status(self, status, previous) -> None:
        self.system_status_calls.append((status, previous))
        if "system_status" in self._raise_on:
            raise DiscordDeliveryError("boom")


class FakeSupabaseClient:
    def __init__(self, existing_session_dates: frozenset[str] = frozenset()):
        self._existing = existing_session_dates

    def table(self, name: str):
        return _FakeTable(self)


class _FakeTable:
    def __init__(self, client: FakeSupabaseClient):
        self._client = client
        self._eq_value: str | None = None

    def select(self, cols: str):
        return self

    def eq(self, col: str, value: str):
        self._eq_value = value
        return self

    def execute(self):
        data = [{"id": "x"}] if self._eq_value in self._client._existing else []
        return SimpleNamespace(data=data)


def _snapshot(system_status: str = "OK") -> IngestionSnapshot:
    return IngestionSnapshot(
        ts=datetime.now(timezone.utc),
        spot_ltp=24500.0,
        futures_ltp=24510.0,
        futures_basis=10.0,
        depth=None,
        option_chain=[],
        india_vix=13.5,
        gift_nifty=None,
        volume=None,
        total_buy_quantity=None,
        total_sell_quantity=None,
        day_high=None,
        day_low=None,
        expiry_date=None,
        system_status=system_status,  # type: ignore[arg-type]
        system_status_detail={},
    )


def _outlook() -> DailyOutlook:
    return DailyOutlook(
        session_date=date(2026, 7, 6),
        predicted_archetype="clean_trend",
        archetype_confidence=0.6,
        contributing_inputs={},
        trend_exhaustion_flag=False,
        straddle_level_vs_history=0.5,
    )


def _transition() -> StateTransition:
    return StateTransition(
        ts=datetime.now(timezone.utc),
        from_state="PREPARE",
        to_state="GO",
        trigger_categories=["volatility"],
        reason="PREPARE->GO driven by volatility; composite=0.65, confirmed after 3 cycles",
    )


def _signal_snapshot():
    return SimpleNamespace(composite_score=0.65)


def _make_scheduler(**overrides) -> tuple[Scheduler, dict]:
    fakes = dict(
        client=FakeSupabaseClient(),
        calendar=FakeCalendar(trading=True),
        ingestion=FakeIngestion(snapshots=[_snapshot()]),
        engine=FakeEngine(results=[(_signal_snapshot(), None)]),
        outlook_generator=FakeOutlookGenerator(_outlook()),
        backfill_job=FakeBackfillJob(),
        discord=FakeDiscord(),
        clock=FakeClock([datetime(2026, 7, 6, 9, 0)]),
    )
    fakes.update(overrides)
    scheduler = Scheduler(
        "http://fake", "fake-key", "http://fake/market", "http://fake/status", **fakes
    )
    return scheduler, fakes


def test_not_a_trading_day_exits_immediately_without_touching_ingestion():
    scheduler, fakes = _make_scheduler(
        calendar=FakeCalendar(trading=False), clock=FakeClock([datetime(2026, 7, 4, 9, 0)])
    )
    scheduler.run()

    assert fakes["ingestion"].started is False
    assert fakes["outlook_generator"].calls == []
    assert fakes["backfill_job"].calls == []


def test_fires_outlook_when_missing_and_posts_to_discord():
    clock = FakeClock(
        [
            datetime(2026, 7, 6, 9, 0),  # today
            datetime(2026, 7, 6, 15, 30),  # pre-open while-check: already >= open -> exits
            datetime(2026, 7, 6, 15, 30),  # live loop while-check: already >= close -> 0 cycles
        ]
    )
    scheduler, fakes = _make_scheduler(client=FakeSupabaseClient(existing_session_dates=set()), clock=clock)
    scheduler.run()

    assert len(fakes["outlook_generator"].calls) == 1
    assert fakes["discord"].outlook_calls == [fakes["outlook_generator"]._outlook]
    assert fakes["engine"].ended is True
    assert fakes["backfill_job"].calls == [date(2026, 7, 6)]


def test_skips_outlook_when_daily_outlook_already_exists():
    clock = FakeClock(
        [
            datetime(2026, 7, 6, 9, 0),
            datetime(2026, 7, 6, 9, 0),  # pre-open check: exists=True short-circuits regardless
            datetime(2026, 7, 6, 15, 30),  # live loop exits immediately
        ]
    )
    scheduler, fakes = _make_scheduler(
        client=FakeSupabaseClient(existing_session_dates={"2026-07-06"}), clock=clock
    )
    scheduler.run()

    assert fakes["outlook_generator"].calls == []
    assert fakes["discord"].outlook_calls == []


def test_live_loop_runs_until_close_and_posts_transition():
    clock = FakeClock(
        [
            datetime(2026, 7, 6, 9, 0),
            datetime(2026, 7, 6, 9, 0),  # pre-open: exists=True, skip
            datetime(2026, 7, 6, 9, 20),  # live loop iter 1
            datetime(2026, 7, 6, 9, 21),  # live loop iter 2
            datetime(2026, 7, 6, 15, 30),  # exit
        ]
    )
    transition = _transition()
    engine = FakeEngine(
        results=[
            (_signal_snapshot(), transition),
            (_signal_snapshot(), None),
        ]
    )
    scheduler, fakes = _make_scheduler(
        client=FakeSupabaseClient(existing_session_dates={"2026-07-06"}),
        clock=clock,
        engine=engine,
        ingestion=FakeIngestion(snapshots=[_snapshot(), _snapshot()]),
    )
    scheduler.run()

    assert engine.call_count == 2
    assert len(fakes["discord"].transition_calls) == 1
    assert fakes["discord"].transition_calls[0][0] is transition


def test_system_status_change_posts_alert_but_not_on_first_cycle():
    clock = FakeClock(
        [
            datetime(2026, 7, 6, 9, 0),
            datetime(2026, 7, 6, 9, 0),
            datetime(2026, 7, 6, 9, 20),
            datetime(2026, 7, 6, 9, 21),
            datetime(2026, 7, 6, 15, 30),
        ]
    )
    engine = FakeEngine(results=[(_signal_snapshot(), None), (_signal_snapshot(), None)])
    ingestion = FakeIngestion(snapshots=[_snapshot("STALE"), _snapshot("OK")])
    scheduler, fakes = _make_scheduler(
        client=FakeSupabaseClient(existing_session_dates={"2026-07-06"}),
        clock=clock,
        engine=engine,
        ingestion=ingestion,
    )
    scheduler.run()

    assert fakes["discord"].system_status_calls == [("OK", "STALE")]


def test_discord_failure_on_transition_does_not_crash_the_loop():
    clock = FakeClock(
        [
            datetime(2026, 7, 6, 9, 0),
            datetime(2026, 7, 6, 9, 0),
            datetime(2026, 7, 6, 9, 20),
            datetime(2026, 7, 6, 15, 30),
        ]
    )
    engine = FakeEngine(results=[(_signal_snapshot(), _transition())])
    scheduler, fakes = _make_scheduler(
        client=FakeSupabaseClient(existing_session_dates={"2026-07-06"}),
        clock=clock,
        engine=engine,
        discord=FakeDiscord(raise_on={"transition"}),
    )
    scheduler.run()  # must not raise

    assert fakes["engine"].ended is True
    assert fakes["backfill_job"].calls == [date(2026, 7, 6)]


def test_cycle_exception_is_caught_and_loop_continues():
    clock = FakeClock(
        [
            datetime(2026, 7, 6, 9, 0),
            datetime(2026, 7, 6, 9, 0),
            datetime(2026, 7, 6, 9, 20),
            datetime(2026, 7, 6, 9, 21),
            datetime(2026, 7, 6, 15, 30),
        ]
    )
    engine = FakeEngine(results=[RuntimeError("transient blip"), (_signal_snapshot(), None)])
    scheduler, fakes = _make_scheduler(
        client=FakeSupabaseClient(existing_session_dates={"2026-07-06"}),
        clock=clock,
        engine=engine,
        ingestion=FakeIngestion(snapshots=[_snapshot(), _snapshot()]),
    )
    scheduler.run()  # must not raise

    assert engine.call_count == 2
    assert fakes["engine"].ended is True
