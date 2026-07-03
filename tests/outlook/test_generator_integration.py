"""Live integration tests for OutlookGenerator (TASK-009 ADR) -- real
Supabase, no mocking. Far-off session_dates so these rows never collide
with real trading data. Each test cleans up the rows it creates.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from dotenv import dotenv_values

from aeolus.ingestion.models import Greeks, IngestionSnapshot, OptionStrike
from aeolus.outlook.generator import OutlookGenerator

ENV_PATH = __file__.rsplit("/tests/", 1)[0] + "/.env"
_cfg = dotenv_values(ENV_PATH)
SUPABASE_URL = _cfg.get("SUPABASE_URL")
SUPABASE_KEY = _cfg.get("SUPABASE_KEY")

pytestmark = pytest.mark.skipif(
    not (SUPABASE_URL and SUPABASE_KEY),
    reason="SUPABASE_URL/SUPABASE_KEY not set in .env",
)

if SUPABASE_URL and SUPABASE_KEY:
    from supabase import create_client

YESTERDAY = date(1999, 9, 1)
SESSION_DATE = date(1999, 9, 2)
FIRST_EVER_DATE = date(1970, 1, 1)


def _greeks() -> Greeks:
    return Greeks(delta=0.5, gamma=0.002, theta=-2.0, vega=1.2)


def _option_chain() -> list[OptionStrike]:
    return [
        OptionStrike(
            strike=24500.0,
            call_oi=60_000,
            put_oi=70_000,
            call_iv=13.5,
            put_iv=13.8,
            call_greeks=_greeks(),
            put_greeks=_greeks(),
        ),
    ]


def _snapshot(expiry_days_out: int = 5) -> IngestionSnapshot:
    return IngestionSnapshot(
        ts=datetime(1999, 9, 2, 8, 45, tzinfo=timezone.utc),
        spot_ltp=24510.0,
        futures_ltp=24515.0,
        futures_basis=5.0,
        depth=None,
        option_chain=_option_chain(),
        india_vix=13.5,
        gift_nifty=None,
        volume=None,
        total_buy_quantity=None,
        total_sell_quantity=None,
        day_high=None,
        day_low=None,
        expiry_date=SESSION_DATE + timedelta(days=expiry_days_out),
        system_status="OK",
        system_status_detail={"ws": "OK", "option_chain": "OK"},
    )


@pytest.fixture
def client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


@pytest.fixture
def generator():
    return OutlookGenerator(SUPABASE_URL, SUPABASE_KEY)


def _cleanup(client):
    client.table("daily_outlook").delete().eq("session_date", SESSION_DATE.isoformat()).execute()
    client.table("signal_snapshots").delete().eq("session_date", YESTERDAY.isoformat()).execute()


def test_run_writes_one_daily_outlook_row_with_trend_exhaustion(client, generator):
    row_id = str(uuid4())
    prior_row = {
        "id": row_id,
        "ts": datetime(1999, 9, 1, 15, 29, tzinfo=timezone.utc).isoformat(),
        "session_date": YESTERDAY.isoformat(),
        "config_type": "NON_EXPIRY",
        "dte": 3,
        "raw_readings": {
            "context": {
                "profile_shape": "trend",
                "_carry": {
                    "day_high": 24700.0,
                    "day_low": 24450.0,
                    "close": 24680.0,
                    "poc": 24600.0,
                    "va_low": 24550.0,
                    "va_high": 24650.0,
                },
            },
            "oi_structure": {
                "pcr_level_and_roc": {"raw_value": 0.1, "reference_band": [0, 1], "sub_score": 0.5, "context": {"pcr_level": 1.05}},
                "_carry": {"max_pain": 24600.0},
            },
        },
        "sub_scores": {},
        "composite_score": 0.5,
        "market_state": "PREPARE",
        "system_status": "OK",
        "reasons": {},
    }
    try:
        client.table("signal_snapshots").insert(prior_row).execute()

        outlook = generator.run(SESSION_DATE, _snapshot())

        assert outlook.session_date == SESSION_DATE
        assert outlook.trend_exhaustion_flag is True
        assert outlook.predicted_archetype in {
            "clean_trend", "grinding_trend", "pinned_range", "choppy_range",
            "breakout_transition", "event_gap", "double_distribution",
        }
        assert 0.0 <= outlook.archetype_confidence <= 1.0
        assert 0.0 <= outlook.straddle_level_vs_history <= 1.0
        assert "archetype_distribution" in outlook.contributing_inputs
        assert "secondary_archetype" in outlook.contributing_inputs
        assert outlook.contributing_inputs["oi_max_pain_carryover"] is not None
        assert outlook.contributing_inputs["prior_close_pcr_level"] == pytest.approx(1.05)

        row = (
            client.table("daily_outlook")
            .select("*")
            .eq("session_date", SESSION_DATE.isoformat())
            .execute()
            .data
        )
        assert len(row) == 1
        assert row[0]["trend_exhaustion_flag"] is True
        assert row[0]["realized_archetype"] is None
    finally:
        _cleanup(client)


def test_first_session_ever_completes_without_raising(generator):
    outlook = OutlookGenerator(SUPABASE_URL, SUPABASE_KEY).run(FIRST_EVER_DATE, _snapshot())
    try:
        assert outlook.trend_exhaustion_flag is False
        assert outlook.straddle_level_vs_history == pytest.approx(0.5) or (
            0.0 <= outlook.straddle_level_vs_history <= 1.0
        )
    finally:
        create_client(SUPABASE_URL, SUPABASE_KEY).table("daily_outlook").delete().eq(
            "session_date", FIRST_EVER_DATE.isoformat()
        ).execute()


def test_duplicate_run_upserts_not_duplicates(client, generator):
    try:
        first = generator.run(SESSION_DATE, _snapshot())
        second = generator.run(SESSION_DATE, _snapshot())

        assert first.session_date == second.session_date

        rows = (
            client.table("daily_outlook")
            .select("*")
            .eq("session_date", SESSION_DATE.isoformat())
            .execute()
            .data
        )
        assert len(rows) == 1
    finally:
        _cleanup(client)


def test_duplicate_run_never_clobbers_a_realized_archetype_backfill(client, generator):
    try:
        generator.run(SESSION_DATE, _snapshot())
        client.table("daily_outlook").update({"realized_archetype": "clean_trend"}).eq(
            "session_date", SESSION_DATE.isoformat()
        ).execute()

        generator.run(SESSION_DATE, _snapshot())

        row = (
            client.table("daily_outlook")
            .select("realized_archetype")
            .eq("session_date", SESSION_DATE.isoformat())
            .execute()
            .data
        )
        assert row[0]["realized_archetype"] == "clean_trend"
    finally:
        _cleanup(client)
