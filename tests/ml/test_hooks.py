"""Contract tests for TASK-021's MLHooks facade. All collaborators (store,
scorer, trainer, dispatcher, registry client) are lightweight fakes -- this
exercises the *orchestration logic* (failure isolation per step, EOD
ordering, go-live/warm-up detection) in isolation, mirroring
tests/scheduler/test_scheduler.py's convention. No DB, no sklearn, no HTTP.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from aeolus.ml.hooks import MLHooks
from aeolus.ml.scorer import ScoreEvent
from aeolus.storage.models import SignalSnapshot

SESSION_DATE = date(2026, 7, 4)


def _snapshot(config_type: str = "NON_EXPIRY") -> SignalSnapshot:
    return SignalSnapshot(
        ts=datetime.now(timezone.utc),
        session_date=SESSION_DATE,
        config_type=config_type,  # type: ignore[arg-type]
        dte=3,
        raw_readings={},
        sub_scores={},
        composite_score=0.5,
        market_state="PREPARE",
        system_status="OK",  # type: ignore[arg-type]
        reasons={},
    )


def _event(kind: str = "ANOMALY_ENTER") -> ScoreEvent:
    return ScoreEvent(kind=kind, score=0.81, z_by_feature={"vix_level": 3.2}, model_version_id=uuid4())


@dataclass
class FakeStore:
    append_calls: list = field(default_factory=list)
    sync_calls: list = field(default_factory=list)
    append_exc: Exception | None = None
    sync_exc: Exception | None = None
    load_window_result: list = field(default_factory=list)
    order_log: list | None = None

    def append(self, snapshot, scaler):
        self.append_calls.append(snapshot)
        if self.append_exc:
            raise self.append_exc
        return True

    def sync_eod(self, session_date):
        self.sync_calls.append(session_date)
        if self.order_log is not None:
            self.order_log.append("sync_eod")
        if self.sync_exc:
            raise self.sync_exc
        return 0

    def load_window(self, config_type, window_days):
        return self.load_window_result


@dataclass
class FakeScorer:
    start_session_calls: list = field(default_factory=list)
    score_calls: list = field(default_factory=list)
    start_session_exc: Exception | None = None
    score_exc: Exception | None = None
    score_result: ScoreEvent | None = None

    def start_session(self, session_date):
        self.start_session_calls.append(session_date)
        if self.start_session_exc:
            raise self.start_session_exc

    def score_cycle(self, snapshot):
        self.score_calls.append(snapshot)
        if self.score_exc:
            raise self.score_exc
        return self.score_result


@dataclass
class FakeTrainer:
    train_calls: int = 0
    train_exc: Exception | None = None
    order_log: list | None = None

    def train_all(self):
        self.train_calls += 1
        if self.order_log is not None:
            self.order_log.append("train_all")
        if self.train_exc:
            raise self.train_exc
        return []


@dataclass
class FakeDispatcher:
    anomaly_calls: list = field(default_factory=list)
    clear_calls: list = field(default_factory=list)
    warmup_calls: list = field(default_factory=list)
    golive_calls: list = field(default_factory=list)
    anomaly_exc: Exception | None = None

    def post_anomaly(self, event, reason, snapshot):
        self.anomaly_calls.append((event, reason, snapshot))
        if self.anomaly_exc:
            raise self.anomaly_exc

    def post_clear(self, event, reason, config_type):
        self.clear_calls.append((event, reason, config_type))

    def post_warmup_progress(self, config_type, day, target):
        self.warmup_calls.append((config_type, day, target))

    def post_golive(self, config_type, model_version):
        self.golive_calls.append((config_type, model_version))


class _FakeTable:
    def __init__(self, client, name):
        self._client = client
        self._name = name
        self._eq: dict[str, str] = {}

    def select(self, cols):
        return self

    def eq(self, col, value):
        self._eq[col] = value
        return self

    def order(self, col, desc=False):
        return self

    def limit(self, n):
        return self

    def execute(self):
        if "id" in self._eq:
            row = self._client.rows_by_id.get(self._eq["id"])
            return SimpleNamespace(data=[row] if row else [])
        if "config_type" in self._eq:
            version = self._client.latest_version_by_config.get(self._eq["config_type"])
            return SimpleNamespace(data=[{"version": version}] if version is not None else [])
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, rows_by_id=None, latest_version_by_config=None):
        self.rows_by_id = rows_by_id or {}
        self.latest_version_by_config = latest_version_by_config or {}

    def table(self, name):
        return _FakeTable(self, name)


def _make_hooks(**overrides) -> tuple[MLHooks, dict]:
    fakes = dict(
        store=FakeStore(),
        scorer=FakeScorer(),
        trainer=FakeTrainer(),
        dispatcher=FakeDispatcher(),
        client=FakeClient(),
    )
    fakes.update(overrides)
    hooks = MLHooks("http://fake", "fake-key", **fakes)
    return hooks, fakes


# --- on_cycle ---------------------------------------------------------


def test_on_cycle_appends_and_scores_with_no_event_posts_nothing():
    hooks, fakes = _make_hooks()
    snapshot = _snapshot()

    hooks.on_cycle(snapshot)

    assert fakes["store"].append_calls == [snapshot]
    assert fakes["scorer"].score_calls == [snapshot]
    assert fakes["dispatcher"].anomaly_calls == []
    assert fakes["dispatcher"].clear_calls == []


def test_on_cycle_append_failure_does_not_prevent_scoring():
    hooks, fakes = _make_hooks(store=FakeStore(append_exc=RuntimeError("boom")))

    hooks.on_cycle(_snapshot())  # must not raise

    assert fakes["scorer"].score_calls != []


def test_on_cycle_score_failure_is_contained():
    hooks, _ = _make_hooks(scorer=FakeScorer(score_exc=RuntimeError("boom")))

    hooks.on_cycle(_snapshot())  # must not raise


def test_on_cycle_posts_anomaly_advisory_with_resolved_version_and_thresholds():
    event = _event("ANOMALY_ENTER")
    client = FakeClient(rows_by_id={str(event.model_version_id): {"version": 3, "flag_threshold": 0.65, "clear_threshold": 0.55}})
    hooks, fakes = _make_hooks(scorer=FakeScorer(score_result=event), client=client)

    hooks.on_cycle(_snapshot())

    assert len(fakes["dispatcher"].anomaly_calls) == 1
    posted_event, reason, snapshot = fakes["dispatcher"].anomaly_calls[0]
    assert posted_event is event
    assert "model v3" in reason
    assert "vix_level" in reason
    assert fakes["dispatcher"].clear_calls == []


def test_on_cycle_posts_clear_advisory_with_config_type():
    event = _event("ANOMALY_CLEAR")
    client = FakeClient(rows_by_id={str(event.model_version_id): {"version": 2, "flag_threshold": 0.65, "clear_threshold": 0.55}})
    hooks, fakes = _make_hooks(scorer=FakeScorer(score_result=event), client=client)

    hooks.on_cycle(_snapshot(config_type="EXPIRY"))

    assert len(fakes["dispatcher"].clear_calls) == 1
    posted_event, reason, config_type = fakes["dispatcher"].clear_calls[0]
    assert posted_event is event
    assert "model v2" in reason
    assert config_type == "EXPIRY"


def test_on_cycle_missing_registry_row_skips_post_without_raising():
    event = _event("ANOMALY_ENTER")
    hooks, fakes = _make_hooks(scorer=FakeScorer(score_result=event), client=FakeClient())

    hooks.on_cycle(_snapshot())  # must not raise

    assert fakes["dispatcher"].anomaly_calls == []


def test_on_cycle_post_failure_is_contained():
    event = _event("ANOMALY_ENTER")
    client = FakeClient(rows_by_id={str(event.model_version_id): {"version": 1, "flag_threshold": 0.65, "clear_threshold": 0.55}})
    hooks, _ = _make_hooks(
        scorer=FakeScorer(score_result=event), client=client, dispatcher=FakeDispatcher(anomaly_exc=RuntimeError("boom"))
    )

    hooks.on_cycle(_snapshot())  # must not raise


# --- on_end_of_day ------------------------------------------------------


def test_on_end_of_day_syncs_before_training():
    order_log: list = []
    hooks, _ = _make_hooks(
        store=FakeStore(order_log=order_log), trainer=FakeTrainer(order_log=order_log)
    )

    hooks.on_end_of_day(SESSION_DATE)

    assert order_log == ["sync_eod", "train_all"]


def test_on_end_of_day_sync_failure_does_not_prevent_training():
    hooks, fakes = _make_hooks(store=FakeStore(sync_exc=RuntimeError("boom")))

    hooks.on_end_of_day(SESSION_DATE)  # must not raise

    assert fakes["trainer"].train_calls == 1


def test_on_end_of_day_train_failure_is_contained():
    hooks, _ = _make_hooks(trainer=FakeTrainer(train_exc=RuntimeError("boom")))

    hooks.on_end_of_day(SESSION_DATE)  # must not raise


# --- start_session (reset + go-live/warm-up detection) ------------------


def test_start_session_resets_scorer():
    hooks, fakes = _make_hooks()

    hooks.start_session(SESSION_DATE)

    assert fakes["scorer"].start_session_calls == [SESSION_DATE]


def test_start_session_scorer_reset_failure_is_contained():
    hooks, _ = _make_hooks(scorer=FakeScorer(start_session_exc=RuntimeError("boom")))

    hooks.start_session(SESSION_DATE)  # must not raise


def test_start_session_posts_warmup_progress_when_no_model_yet():
    rows = [SimpleNamespace(session_date=date(2026, 7, d)) for d in range(1, 8)]  # 7 distinct days
    hooks, fakes = _make_hooks(store=FakeStore(load_window_result=rows), client=FakeClient())

    hooks.start_session(SESSION_DATE)

    assert fakes["dispatcher"].warmup_calls == [("EXPIRY", 7, 15), ("NON_EXPIRY", 7, 15)]
    assert fakes["dispatcher"].golive_calls == []


def test_start_session_posts_nothing_when_no_data_collected_yet():
    hooks, fakes = _make_hooks(store=FakeStore(load_window_result=[]), client=FakeClient())

    hooks.start_session(SESSION_DATE)

    assert fakes["dispatcher"].warmup_calls == []
    assert fakes["dispatcher"].golive_calls == []


def test_start_session_posts_golive_when_version_is_exactly_one():
    client = FakeClient(latest_version_by_config={"EXPIRY": 1, "NON_EXPIRY": 1})
    hooks, fakes = _make_hooks(client=client)

    hooks.start_session(SESSION_DATE)

    assert set(fakes["dispatcher"].golive_calls) == {("EXPIRY", 1), ("NON_EXPIRY", 1)}
    assert fakes["dispatcher"].warmup_calls == []


def test_start_session_posts_nothing_when_already_live_past_v1():
    client = FakeClient(latest_version_by_config={"EXPIRY": 5, "NON_EXPIRY": 5})
    hooks, fakes = _make_hooks(client=client)

    hooks.start_session(SESSION_DATE)

    assert fakes["dispatcher"].golive_calls == []
    assert fakes["dispatcher"].warmup_calls == []


def test_start_session_configs_are_independent():
    client = FakeClient(latest_version_by_config={"EXPIRY": 1})
    rows = [SimpleNamespace(session_date=date(2026, 7, 1))]
    hooks, fakes = _make_hooks(store=FakeStore(load_window_result=rows), client=client)

    hooks.start_session(SESSION_DATE)

    assert fakes["dispatcher"].golive_calls == [("EXPIRY", 1)]
    assert fakes["dispatcher"].warmup_calls == [("NON_EXPIRY", 1, 15)]


def test_start_session_announcement_failure_is_contained():
    client = FakeClient(latest_version_by_config={"EXPIRY": 1, "NON_EXPIRY": 1})
    hooks, _ = _make_hooks(client=client)

    def _broken_post_golive(config_type, model_version):
        raise RuntimeError("boom")

    hooks._dispatcher.post_golive = _broken_post_golive  # noqa: SLF001 -- direct fault injection

    hooks.start_session(SESSION_DATE)  # must not raise
