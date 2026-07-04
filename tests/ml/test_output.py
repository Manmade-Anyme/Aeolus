"""Contract tests for TASK-020's MLDiscordDispatcher. HTTP boundary mocked via
httpx.MockTransport (same convention as tests/output/test_discord.py) -- no
internal mocking, only the wire boundary is faked."""

import json
from datetime import date, datetime, timezone
from uuid import uuid4

import httpx
import pytest

from aeolus.ml.output import MLDiscordDeliveryError, MLDiscordDispatcher
from aeolus.ml.scorer import ScoreEvent
from aeolus.storage.models import SignalSnapshot

_ENGINE_COLORS = {0x2ECC71, 0xF1C40F, 0xE74C3C, 0x9B59B6}


def _snapshot(**overrides) -> SignalSnapshot:
    defaults = dict(
        ts=datetime.now(timezone.utc),
        session_date=date(2026, 7, 4),
        config_type="NON_EXPIRY",
        dte=3,
        raw_readings={},
        sub_scores={},
        composite_score=0.5,
        market_state="PREPARE",
        system_status="OK",
        reasons={},
    )
    defaults.update(overrides)
    return SignalSnapshot(**defaults)


def _event(**overrides) -> ScoreEvent:
    defaults = dict(kind="ANOMALY_ENTER", score=0.81, z_by_feature={"vix_level": 3.2}, model_version_id=uuid4())
    defaults.update(overrides)
    return ScoreEvent(**defaults)


class _Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, headers = self.responses.pop(0)
        return httpx.Response(status, headers=headers or {}, request=request)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("aeolus.ml.output.time.sleep", lambda _seconds: None)


def test_post_anomaly_is_visually_distinct_and_carries_advisory_footer():
    recorder = _Recorder([(204, None)])
    dispatcher = MLDiscordDispatcher("https://discord.test/ml", transport=httpx.MockTransport(recorder))
    reason = "anomalous — driven by vix_level (+3.2σ), gex_magnitude (-2.8σ) | score 0.812 vs flag 0.650 | model v3"

    dispatcher.post_anomaly(_event(), reason, _snapshot(config_type="EXPIRY"))

    assert len(recorder.requests) == 1
    body = json.loads(recorder.requests[0].content.decode())
    embed = body["embeds"][0]
    assert "\U0001f52c ML" in embed["title"]
    assert "EXPIRY" in embed["title"]
    assert reason in embed["description"]
    assert embed["color"] not in _ENGINE_COLORS
    assert embed["footer"]["text"] == "advisory only — does not change engine state"


def test_post_clear_is_single_line_no_advisory_footer():
    recorder = _Recorder([(204, None)])
    dispatcher = MLDiscordDispatcher("https://discord.test/ml", transport=httpx.MockTransport(recorder))
    reason = "cleared — score 0.412 vs clear 0.550 | model v3"

    dispatcher.post_clear(_event(kind="ANOMALY_CLEAR"), reason, "NON_EXPIRY")

    body = json.loads(recorder.requests[0].content.decode())
    embed = body["embeds"][0]
    assert "Cleared" in embed["title"] and "NON_EXPIRY" in embed["title"]
    assert reason in embed["description"]
    assert "footer" not in embed
    assert embed["color"] not in _ENGINE_COLORS


def test_post_warmup_progress_content():
    recorder = _Recorder([(204, None)])
    dispatcher = MLDiscordDispatcher("https://discord.test/ml", transport=httpx.MockTransport(recorder))

    dispatcher.post_warmup_progress("NON_EXPIRY", day=7, target=15)

    body = json.loads(recorder.requests[0].content.decode())
    embed = body["embeds"][0]
    assert "day 7 of ~15" in embed["description"]
    assert embed["color"] not in _ENGINE_COLORS


def test_post_warmup_progress_suppresses_second_call_same_day():
    recorder = _Recorder([(204, None)])
    dispatcher = MLDiscordDispatcher("https://discord.test/ml", transport=httpx.MockTransport(recorder))

    dispatcher.post_warmup_progress("NON_EXPIRY", day=7, target=15)
    dispatcher.post_warmup_progress("NON_EXPIRY", day=7, target=15)

    assert len(recorder.requests) == 1


def test_post_warmup_progress_allows_next_day():
    recorder = _Recorder([(204, None), (204, None)])
    dispatcher = MLDiscordDispatcher("https://discord.test/ml", transport=httpx.MockTransport(recorder))

    dispatcher.post_warmup_progress("NON_EXPIRY", day=7, target=15)
    dispatcher.post_warmup_progress("NON_EXPIRY", day=8, target=15)

    assert len(recorder.requests) == 2


def test_post_warmup_progress_configs_are_independent():
    recorder = _Recorder([(204, None), (204, None)])
    dispatcher = MLDiscordDispatcher("https://discord.test/ml", transport=httpx.MockTransport(recorder))

    dispatcher.post_warmup_progress("NON_EXPIRY", day=7, target=15)
    dispatcher.post_warmup_progress("EXPIRY", day=7, target=15)

    assert len(recorder.requests) == 2


def test_post_golive_content():
    recorder = _Recorder([(204, None)])
    dispatcher = MLDiscordDispatcher("https://discord.test/ml", transport=httpx.MockTransport(recorder))

    dispatcher.post_golive("EXPIRY", model_version=1)

    body = json.loads(recorder.requests[0].content.decode())
    embed = body["embeds"][0]
    assert "Model Live" in embed["title"] and "EXPIRY" in embed["title"]
    assert "v1" in embed["description"]
    assert embed["color"] not in _ENGINE_COLORS


def test_unset_webhook_never_posts_and_never_crashes():
    dispatcher = MLDiscordDispatcher(None)

    dispatcher.post_anomaly(_event(), "anomalous — ...", _snapshot())
    dispatcher.post_clear(_event(kind="ANOMALY_CLEAR"), "cleared — ...", "NON_EXPIRY")
    dispatcher.post_warmup_progress("NON_EXPIRY", day=1, target=15)
    dispatcher.post_golive("NON_EXPIRY", model_version=1)
    # No assertions beyond "did not raise" -- there is no client/transport to inspect.


def test_delivery_failure_raises_ml_discord_delivery_error_only():
    recorder = _Recorder([(500, None), (500, None), (500, None), (500, None)])
    dispatcher = MLDiscordDispatcher("https://discord.test/ml", transport=httpx.MockTransport(recorder))

    with pytest.raises(MLDiscordDeliveryError):
        dispatcher.post_golive("NON_EXPIRY", model_version=1)

    assert len(recorder.requests) == 4


def test_no_retry_on_400_raises_immediately():
    recorder = _Recorder([(400, None)])
    dispatcher = MLDiscordDispatcher("https://discord.test/ml", transport=httpx.MockTransport(recorder))

    with pytest.raises(MLDiscordDeliveryError):
        dispatcher.post_golive("NON_EXPIRY", model_version=1)

    assert len(recorder.requests) == 1
