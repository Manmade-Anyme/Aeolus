"""Live integration tests for Engine (TASK-008 ADR) -- real Supabase, no
mocking. Uses a far-off session_date so these rows never collide with real
trading data. Each test cleans up the rows it creates.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from dotenv import dotenv_values

from aeolus.engine.engine import Engine
from aeolus.ingestion.models import Greeks, IngestionSnapshot, OptionStrike

ENV_PATH = __file__.rsplit("/tests/", 1)[0] + "/.env"
_cfg = dotenv_values(ENV_PATH)
SUPABASE_URL = _cfg.get("SUPABASE_URL")
SUPABASE_KEY = _cfg.get("SUPABASE_KEY")

pytestmark = pytest.mark.skipif(
    not (SUPABASE_URL and SUPABASE_KEY),
    reason="SUPABASE_URL/SUPABASE_KEY not set in .env",
)

SESSION_DATE = date(1999, 6, 15)
LOT_SIZE = 65


def _greeks(delta: float, gamma: float) -> Greeks:
    return Greeks(delta=delta, gamma=gamma, theta=-2.0, vega=1.2)


def _option_chain() -> list[OptionStrike]:
    return [
        OptionStrike(
            strike=24400.0,
            call_oi=50_000,
            put_oi=80_000,
            call_iv=14.0,
            put_iv=14.5,
            call_greeks=_greeks(0.7, 0.002),
            put_greeks=_greeks(-0.3, 0.002),
        ),
        OptionStrike(
            strike=24500.0,
            call_oi=90_000,
            put_oi=90_000,
            call_iv=13.5,
            put_iv=13.6,
            call_greeks=_greeks(0.5, 0.004),
            put_greeks=_greeks(-0.5, 0.004),
        ),
        OptionStrike(
            strike=24600.0,
            call_oi=80_000,
            put_oi=50_000,
            call_iv=13.8,
            put_iv=14.1,
            call_greeks=_greeks(0.3, 0.002),
            put_greeks=_greeks(-0.7, 0.002),
        ),
    ]


def _snapshot(ts: datetime, futures_ltp: float = 24500.0, volume: int = 500_000) -> IngestionSnapshot:
    return IngestionSnapshot(
        ts=ts,
        spot_ltp=24495.0,
        futures_ltp=futures_ltp,
        futures_basis=futures_ltp - 24495.0,
        depth=None,
        option_chain=_option_chain(),
        india_vix=13.0,
        gift_nifty=None,
        volume=volume,
        total_buy_quantity=250_000,
        total_sell_quantity=250_000,
        day_high=24550.0,
        day_low=24450.0,
        expiry_date=SESSION_DATE + timedelta(days=5),  # non-expiry cycle (dte=5)
        system_status="OK",
        system_status_detail={"ws": "OK", "option_chain": "OK"},
    )


@pytest.fixture
def engine():
    eng = Engine(SUPABASE_URL, SUPABASE_KEY)
    eng.start(SESSION_DATE)
    yield eng
    eng._client.table("signal_snapshots").delete().eq("session_date", SESSION_DATE.isoformat()).execute()
    eng._client.table("state_transitions").delete().eq(
        "reason", "test-marker-not-a-real-transition"
    ).execute()


def test_run_cycle_writes_a_signal_snapshot_row(engine):
    snapshot = _snapshot(datetime(1999, 6, 15, 9, 20, tzinfo=timezone.utc))
    result, transition = engine.run_cycle(snapshot, LOT_SIZE)
    assert transition is None  # first-ever cycle, nothing to flip from

    assert result.session_date == SESSION_DATE
    assert result.config_type == "NON_EXPIRY"
    assert result.dte == 5
    assert result.market_state in {"NO_GO", "PREPARE", "GO"}
    assert result.system_status == "OK"

    expected_sub_signals = {
        "iv_percentile_rank",
        "iv_rv_spread",
        "vix_level_and_roc",
        "expected_move_consumed_ratio",
        "gex_regime",
        "spot_distance_from_flip",
        "pcr_level_and_roc",
        "oi_buildup_classification",
        "oi_wall_proximity_and_strength",
        "max_pain_drift",
        "cvd_direction_and_divergence",
        "delta_imbalance_and_absorption",
        "volume_participation_range",
        "prior_day_profile_shape",
        "gap_classification",
        "futures_basis_drift",
    }
    assert set(result.reasons) == expected_sub_signals
    assert set(result.sub_scores) == {"volatility", "gamma", "oi_structure", "order_flow", "context"}

    row = (
        engine._client.table("signal_snapshots")
        .select("*")
        .eq("session_date", SESSION_DATE.isoformat())
        .execute()
        .data
    )
    assert len(row) == 1
    assert row[0]["composite_score"] == pytest.approx(result.composite_score)


def test_second_cycle_uses_first_as_previous_snapshot(engine):
    """oi_structure/order_flow's cycle-relative functions need a real previous
    snapshot -- confirms the engine actually threads state.previous_snapshot
    through, not just that it degrades gracefully on cycle 1."""
    engine.run_cycle(_snapshot(datetime(1999, 6, 15, 9, 20, tzinfo=timezone.utc), futures_ltp=24500.0), LOT_SIZE)
    second, _ = engine.run_cycle(
        _snapshot(datetime(1999, 6, 15, 9, 21, tzinfo=timezone.utc), futures_ltp=24520.0), LOT_SIZE
    )
    pcr_reading = second.raw_readings["oi_structure"]["pcr_level_and_roc"]
    assert pcr_reading["raw_value"] is not None  # previous is no longer None on cycle 2


def test_hysteresis_flip_writes_state_transition(engine):
    """Force the pre-existing confirmed_state to something a realistic
    synthetic snapshot is very unlikely to compute to (GO), then run
    confirmation_cycles identical cycles -- the engine must flip away and
    write exactly one state_transitions row, using its own scorer.apply_hysteresis
    wiring end to end."""
    engine._state.confirmed_state = "GO"
    engine._state.pending_state = "GO"

    last_result = None
    last_transition = None
    for i in range(3):
        last_result, last_transition = engine.run_cycle(
            _snapshot(datetime(1999, 6, 15, 9, 20 + i, tzinfo=timezone.utc)), LOT_SIZE
        )

    assert last_result.market_state != "GO"
    assert last_transition is not None
    assert last_transition.from_state == "GO"
    assert last_transition.to_state == last_result.market_state

    transitions = (
        engine._client.table("state_transitions")
        .select("*")
        .eq("from_state", "GO")
        .eq("to_state", last_result.market_state)
        .order("ts", desc=True)
        .limit(1)
        .execute()
        .data
    )
    assert len(transitions) == 1
    engine._client.table("state_transitions").delete().eq("id", transitions[0]["id"]).execute()


def test_end_session_clears_session_scoped_state_only(engine):
    engine.run_cycle(_snapshot(datetime(1999, 6, 15, 9, 20, tzinfo=timezone.utc)), LOT_SIZE)
    assert engine._state.price_history  # non-empty after a real cycle

    cross_session_snapshot = list(engine._state.trailing_iv_history)
    engine.end_session()

    assert engine._state.price_history == []
    assert engine._state.previous_snapshot is None
    assert engine._state.established_range is None
    assert engine._state.trailing_iv_history == cross_session_snapshot  # untouched
