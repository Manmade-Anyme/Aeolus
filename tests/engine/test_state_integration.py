"""Live integration tests for EngineState.load() (TASK-008 ADR Decision §4) --
real Supabase, no mocking. Uses far-off session_dates so these rows never
collide with real trading data. Each test cleans up the rows it creates.
"""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from dotenv import dotenv_values

from aeolus.engine.state import EngineState

ENV_PATH = __file__.rsplit("/tests/", 1)[0] + "/.env"
_cfg = dotenv_values(ENV_PATH)
SUPABASE_URL = _cfg.get("SUPABASE_URL")
SUPABASE_KEY = _cfg.get("SUPABASE_KEY")

pytestmark = pytest.mark.skipif(
    not (SUPABASE_URL and SUPABASE_KEY),
    reason="SUPABASE_URL/SUPABASE_KEY not set in .env",
)

if SUPABASE_URL and SUPABASE_KEY:
    from supabase import Client, create_client

TABLE = "signal_snapshots"
YESTERDAY = date(1999, 3, 1)
TODAY = date(1999, 3, 2)


@pytest.fixture(scope="module")
def client() -> "Client":
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _row(row_id: str, session_date: date, ts: datetime, raw_readings: dict, market_state: str = "NO_GO"):
    return {
        "id": row_id,
        "ts": ts.isoformat(),
        "session_date": session_date.isoformat(),
        "config_type": "NON_EXPIRY",
        "dte": 3,
        "raw_readings": raw_readings,
        "sub_scores": {"volatility": 0.5, "gamma": 0.5, "oi_structure": 0.5, "order_flow": 0.5, "context": 0.5},
        "composite_score": 0.5,
        "market_state": market_state,
        "system_status": "OK",
        "reasons": {},
    }


def test_seeds_cross_session_trailing_histories_and_prior_day_context(client):
    row_id = str(uuid4())
    raw_readings = {
        "volatility": {
            "iv_percentile_rank": {"raw_value": 14.0, "reference_band": [10, 18], "sub_score": 0.5},
            "_carry": {"current_iv": 14.0, "spot_ltp": 24500.0, "india_vix": 13.0},
        },
        "gamma": {"gex_regime": {"raw_value": 1_000_000.0, "reference_band": [0, 1], "sub_score": 0.6}},
        "oi_structure": {"pcr_level_and_roc": {"raw_value": 0.1, "reference_band": [0, 1], "sub_score": 0.5}},
        "order_flow": {
            "cvd_direction_and_divergence": {"raw_value": 500.0, "reference_band": [0, 1], "sub_score": 0.6},
            "_carry": {"volume": 12_000_000.0},
        },
        "context": {
            "gap_classification": {"raw_value": 10.0, "reference_band": [0, 1], "sub_score": 0.5},
            "_carry": {
                "day_high": 24700.0,
                "day_low": 24400.0,
                "close": 24650.0,
                "poc": 24600.0,
                "va_low": 24550.0,
                "va_high": 24650.0,
            },
        },
    }
    try:
        client.table(TABLE).insert(
            _row(row_id, YESTERDAY, datetime(1999, 3, 1, 10, 0, tzinfo=timezone.utc), raw_readings)
        ).execute()

        state = EngineState.load(client, TODAY)

        assert state.trailing_iv_history == [14.0]
        assert state.trailing_spot_history == [24500.0]
        assert state.trailing_vix_history == [13.0]
        assert state.trailing_gex_magnitude_history == [1_000_000.0]
        assert state.trailing_pcr_roc_magnitude_history == [0.1]
        assert state.trailing_cvd_magnitude_history == [500.0]
        assert state.average_daily_volume == pytest.approx(12_000_000.0)
        assert state.prior_day_high == 24700.0
        assert state.prior_day_low == 24400.0
        assert state.prior_close == 24650.0
        assert state.prior_value_area == (24600.0, 24550.0, 24650.0)
        # session-scoped fields are NOT seeded from a prior (different) session_date
        assert state.cvd_delta_history == []
        assert state.session_open is None
    finally:
        client.table(TABLE).delete().eq("id", row_id).execute()


def test_first_session_ever_seeds_empty_without_raising(client):
    state = EngineState.load(client, date(1970, 1, 1))
    assert state.trailing_iv_history == []
    assert state.prior_value_area is None
    assert state.average_daily_volume is None


def test_same_day_restart_rebuilds_session_scoped_state(client):
    row_a, row_b = str(uuid4()), str(uuid4())
    raw_a = {
        "order_flow": {
            "cvd_direction_and_divergence": {"raw_value": 0.0, "reference_band": [0, 1], "sub_score": 0.5},
            "_carry": {"cvd_delta": 100.0, "futures_ltp": 24500.0, "futures_basis": 10.0},
        },
        "context": {"_carry": {"futures_ltp": 24500.0, "volume_delta": 200.0, "spot_ltp": 24490.0}},
    }
    raw_b = {
        "order_flow": {
            "cvd_direction_and_divergence": {"raw_value": 100.0, "reference_band": [0, 1], "sub_score": 0.6},
            "_carry": {
                "cvd_delta": 150.0,
                "futures_ltp": 24510.0,
                "futures_basis": 12.0,
                "established_range_low": 24400.0,
                "established_range_high": 24600.0,
            },
        },
        "context": {"_carry": {"futures_ltp": 24510.0, "volume_delta": 300.0, "spot_ltp": 24505.0}},
    }
    try:
        client.table(TABLE).insert(
            _row(row_a, TODAY, datetime(1999, 3, 2, 9, 16, tzinfo=timezone.utc), raw_a)
        ).execute()
        client.table(TABLE).insert(
            _row(row_b, TODAY, datetime(1999, 3, 2, 9, 17, tzinfo=timezone.utc), raw_b, market_state="PREPARE")
        ).execute()

        state = EngineState.load(client, TODAY)

        assert state.cvd_delta_history == [100.0, 150.0]
        assert state.price_history == [24500.0, 24510.0]
        assert state.basis_history == [10.0, 12.0]
        assert state.established_range == (24400.0, 24600.0)
        assert state.cycle_price_volume_history == [(24500.0, 200.0), (24510.0, 300.0)]
        assert state.session_open == 24500.0
        assert state.session_reference_price == 24490.0
        assert state.confirmed_state == "PREPARE"
        assert state.pending_streak == 0
    finally:
        client.table(TABLE).delete().eq("id", row_a).execute()
        client.table(TABLE).delete().eq("id", row_b).execute()
