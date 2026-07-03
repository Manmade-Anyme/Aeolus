from datetime import date, datetime, timezone

import httpx
import pytest

from aeolus.output.discord import DiscordDeliveryError, DiscordDispatcher
from aeolus.storage.models import DailyOutlook, SignalSnapshot, StateTransition


def _snapshot(**overrides) -> SignalSnapshot:
    defaults = dict(
        ts=datetime.now(timezone.utc),
        session_date=date(2026, 7, 3),
        config_type="NON_EXPIRY",
        dte=3,
        raw_readings={
            "volatility": {
                "iv_percentile_rank": {"raw_value": 42.5, "reference_band": [20.0, 80.0], "sub_score": 0.6},
                "iv_rv_spread": {"raw_value": 1.2, "reference_band": [-5.0, 5.0], "sub_score": 0.55},
                "vix_level_and_roc": {"raw_value": 14.0, "reference_band": [10.0, 30.0], "sub_score": 0.5},
                "expected_move_consumed_ratio": {"raw_value": 0.4, "reference_band": [0.0, 1.0], "sub_score": 0.4},
                "_carry": {"current_iv": 14.0},
            },
            "gamma": {
                "gex_regime": {"raw_value": 1.0, "reference_band": [-1.0, 1.0], "sub_score": 0.6},
                "spot_distance_from_flip": {"raw_value": 0.2, "reference_band": [0.0, 1.0], "sub_score": 0.5},
            },
        },
        sub_scores={"volatility": 0.51, "gamma": 0.55},
        composite_score=0.53,
        market_state="PREPARE",
        system_status="OK",
        reasons={
            "iv_percentile_rank": "iv_percentile_rank: 42.50 (band 20.00-80.00, score 0.60)",
            "iv_rv_spread": "iv_rv_spread: 1.20 (band -5.00-5.00, score 0.55)",
            "vix_level_and_roc": "vix_level_and_roc: 14.00 (band 10.00-30.00, score 0.50)",
            "expected_move_consumed_ratio": "expected_move_consumed_ratio: 0.40 (band 0.00-1.00, score 0.40)",
            "gex_regime": "gex_regime: 1.00 (band -1.00-1.00, score 0.60)",
            "spot_distance_from_flip": "spot_distance_from_flip: 0.20 (band 0.00-1.00, score 0.50)",
        },
    )
    defaults.update(overrides)
    return SignalSnapshot(**defaults)


def _transition(**overrides) -> StateTransition:
    defaults = dict(
        ts=datetime.now(timezone.utc),
        from_state="PREPARE",
        to_state="GO",
        trigger_categories=["gamma", "volatility"],
        reason="PREPARE->GO driven by gamma, volatility; composite=0.71, confirmed after 3 cycles",
    )
    defaults.update(overrides)
    return StateTransition(**defaults)


def _outlook(**overrides) -> DailyOutlook:
    defaults = dict(
        session_date=date(2026, 7, 3),
        predicted_archetype="clean_trend",
        archetype_confidence=0.62,
        contributing_inputs={
            "archetype_distribution": {"clean_trend": 0.62, "grinding_trend": 0.2},
            "secondary_archetype": "grinding_trend",
            "secondary_confidence": 0.2,
            "gift_nifty_gap": 12.5,
            "iv_percentile_heading_in": 55.0,
            "vix_level_and_roc_heading_in": 13.5,
            "oi_max_pain_carryover": 25000.0,
            "prior_close_pcr_level": 1.1,
            "futures_gap": 20.0,
            "inside_prior_value_area": True,
            "dte": 3,
        },
        trend_exhaustion_flag=False,
        straddle_level_vs_history=0.1,
    )
    defaults.update(overrides)
    return DailyOutlook(**defaults)


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
    monkeypatch.setattr("aeolus.output.discord.time.sleep", lambda _seconds: None)


def test_post_outlook_hits_market_webhook_with_forecast_and_inputs():
    recorder = _Recorder([(204, None)])
    dispatcher = DiscordDispatcher(
        "https://discord.test/market", "https://discord.test/status", transport=httpx.MockTransport(recorder)
    )
    dispatcher.post_outlook(_outlook())

    assert len(recorder.requests) == 1
    assert str(recorder.requests[0].url) == "https://discord.test/market"
    body = recorder.requests[0].content.decode()
    assert "clean_trend" in body
    assert "0.62" in body


def test_post_outlook_trend_exhaustion_lead_line():
    recorder = _Recorder([(204, None)])
    dispatcher = DiscordDispatcher(
        "https://discord.test/market", "https://discord.test/status", transport=httpx.MockTransport(recorder)
    )
    dispatcher.post_outlook(_outlook(trend_exhaustion_flag=True))

    body = recorder.requests[0].content.decode()
    assert "elongated trend day" in body


def test_post_transition_includes_category_breakdown_and_confirms():
    recorder = _Recorder([(204, None)])
    dispatcher = DiscordDispatcher(
        "https://discord.test/market", "https://discord.test/status", transport=httpx.MockTransport(recorder)
    )
    dispatcher.post_transition(_transition(), _snapshot(), _outlook())

    body = recorder.requests[0].content.decode()
    assert "iv_percentile_rank: 42.50" in body
    assert "gex_regime: 1.00" in body
    assert "current_iv" not in body  # _carry filtered out
    assert "confirms clean_trend outlook" in body


def test_post_transition_diverges_from_outlook():
    recorder = _Recorder([(204, None)])
    dispatcher = DiscordDispatcher(
        "https://discord.test/market", "https://discord.test/status", transport=httpx.MockTransport(recorder)
    )
    dispatcher.post_transition(
        _transition(to_state="NO_GO"), _snapshot(market_state="NO_GO"), _outlook()
    )

    body = recorder.requests[0].content.decode()
    assert "diverges from clean_trend outlook" in body


def test_post_transition_no_outlook_available():
    recorder = _Recorder([(204, None)])
    dispatcher = DiscordDispatcher(
        "https://discord.test/market", "https://discord.test/status", transport=httpx.MockTransport(recorder)
    )
    dispatcher.post_transition(_transition(), _snapshot(), None)

    body = recorder.requests[0].content.decode()
    assert "no Outlook available for today" in body


def test_post_transition_mixed_archetype_not_directly_comparable():
    recorder = _Recorder([(204, None)])
    dispatcher = DiscordDispatcher(
        "https://discord.test/market", "https://discord.test/status", transport=httpx.MockTransport(recorder)
    )
    dispatcher.post_transition(_transition(), _snapshot(), _outlook(predicted_archetype="event_gap"))

    body = recorder.requests[0].content.decode()
    assert "not directly comparable" in body


def test_post_system_status_hits_status_webhook_only_and_is_distinct():
    recorder = _Recorder([(204, None)])
    dispatcher = DiscordDispatcher(
        "https://discord.test/market", "https://discord.test/status", transport=httpx.MockTransport(recorder)
    )
    dispatcher.post_system_status("DISCONNECTED", "OK")

    assert len(recorder.requests) == 1
    assert str(recorder.requests[0].url) == "https://discord.test/status"
    body = recorder.requests[0].content.decode()
    assert "SYSTEM STATUS" in body
    import json

    color = json.loads(body)["embeds"][0]["color"]
    assert color not in (0x2ECC71, 0xF1C40F, 0xE74C3C)  # not any market-state color


def test_retries_on_5xx_then_succeeds():
    recorder = _Recorder([(500, None), (500, None), (204, None)])
    dispatcher = DiscordDispatcher(
        "https://discord.test/market", "https://discord.test/status", transport=httpx.MockTransport(recorder)
    )
    dispatcher.post_system_status("STALE", "OK")

    assert len(recorder.requests) == 3


def test_retries_on_429_honoring_retry_after():
    recorder = _Recorder([(429, {"Retry-After": "0"}), (204, None)])
    dispatcher = DiscordDispatcher(
        "https://discord.test/market", "https://discord.test/status", transport=httpx.MockTransport(recorder)
    )
    dispatcher.post_system_status("STALE", "OK")

    assert len(recorder.requests) == 2


def test_no_retry_on_400_raises_immediately():
    recorder = _Recorder([(400, None)])
    dispatcher = DiscordDispatcher(
        "https://discord.test/market", "https://discord.test/status", transport=httpx.MockTransport(recorder)
    )
    with pytest.raises(DiscordDeliveryError):
        dispatcher.post_system_status("STALE", "OK")

    assert len(recorder.requests) == 1


def test_exhausted_retries_raises_delivery_error():
    recorder = _Recorder([(500, None), (500, None), (500, None), (500, None)])
    dispatcher = DiscordDispatcher(
        "https://discord.test/market", "https://discord.test/status", transport=httpx.MockTransport(recorder)
    )
    with pytest.raises(DiscordDeliveryError):
        dispatcher.post_system_status("STALE", "OK")

    assert len(recorder.requests) == 4
