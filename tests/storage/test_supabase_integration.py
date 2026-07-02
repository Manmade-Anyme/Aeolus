"""Live integration tests against the real Supabase project (same access pattern
as Ares: supabase-py client + anon key, RLS disabled on these tables — see
docs/DATA_MODEL.md and Ares's docs/07_Storage.md). No internal mocking — every
test hits the actual REST API. Each test cleans up the rows it creates.

Requires SUPABASE_URL / SUPABASE_KEY in .env (already present) and the schema
from supabase/migrations/*.sql applied (one-time, via Supabase SQL Editor —
anon key cannot run DDL). Skipped if credentials are missing.
"""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from postgrest.exceptions import APIError

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


@pytest.fixture(scope="module")
def client() -> "Client":
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def test_signal_snapshot_round_trip_recomputable(client):
    row_id = str(uuid4())
    payload = {
        "id": row_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_date": date.today().isoformat(),
        "config_type": "EXPIRY",
        "dte": 0,
        "raw_readings": {"gamma": {"raw_value": 0.85, "reference_band": [0.5, 1.2]}},
        "sub_scores": {"gamma": 0.4},
        "composite_score": 0.4,
        "market_state": "GO",
        "system_status": "OK",
        "reasons": {"gamma": "0.85 within [0.5, 1.2] -> 0.40"},
    }
    try:
        client.table("signal_snapshots").insert(payload).execute()
        resp = client.table("signal_snapshots").select("*").eq("id", row_id).execute()
        assert len(resp.data) == 1
        row = resp.data[0]
        assert row["raw_readings"]["gamma"]["raw_value"] == 0.85
        assert row["sub_scores"]["gamma"] == 0.4
        assert row["market_state"] == "GO"
        assert row["system_status"] == "OK"
    finally:
        client.table("signal_snapshots").delete().eq("id", row_id).execute()


def test_signal_snapshot_rejects_invalid_market_state(client):
    row_id = str(uuid4())
    payload = {
        "id": row_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_date": date.today().isoformat(),
        "config_type": "EXPIRY",
        "dte": 0,
        "raw_readings": {},
        "sub_scores": {},
        "composite_score": 0.0,
        "market_state": "MAYBE_GO",  # invalid enum value
        "system_status": "OK",
        "reasons": {},
    }
    with pytest.raises(APIError):
        client.table("signal_snapshots").insert(payload).execute()


def test_state_transition_insert(client):
    row_id = str(uuid4())
    payload = {
        "id": row_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "from_state": "NO_GO",
        "to_state": "GO",
        "trigger_categories": ["gamma", "order_flow"],
        "reason": "composite crossed 0.65, driven by gamma+order_flow",
    }
    try:
        client.table("state_transitions").insert(payload).execute()
        resp = client.table("state_transitions").select("*").eq("id", row_id).execute()
        assert resp.data[0]["trigger_categories"] == ["gamma", "order_flow"]
    finally:
        client.table("state_transitions").delete().eq("id", row_id).execute()


def test_daily_outlook_trend_exhaustion_flag_own_column(client):
    row_id = str(uuid4())
    unique_date = date(1999, 1, 1).isoformat()  # far from real trading dates
    payload = {
        "id": row_id,
        "session_date": unique_date,
        "predicted_archetype": "clean_trend",
        "archetype_confidence": 0.72,
        "contributing_inputs": {"straddle_iv": 12.4},
        "trend_exhaustion_flag": True,
        "straddle_level_vs_history": 1.1,
    }
    try:
        client.table("daily_outlook").insert(payload).execute()
        resp = client.table("daily_outlook").select("*").eq("id", row_id).execute()
        row = resp.data[0]
        assert row["trend_exhaustion_flag"] is True
        assert "trend_exhaustion_flag" not in row["contributing_inputs"]
        assert row["realized_archetype"] is None
    finally:
        client.table("daily_outlook").delete().eq("id", row_id).execute()


def test_daily_outlook_session_date_unique(client):
    row_id_a, row_id_b = str(uuid4()), str(uuid4())
    unique_date = date(1999, 1, 2).isoformat()
    base = {
        "session_date": unique_date,
        "predicted_archetype": "clean_trend",
        "archetype_confidence": 0.5,
        "contributing_inputs": {},
        "trend_exhaustion_flag": False,
        "straddle_level_vs_history": 1.0,
    }
    try:
        client.table("daily_outlook").insert({**base, "id": row_id_a}).execute()
        with pytest.raises(APIError):
            client.table("daily_outlook").insert({**base, "id": row_id_b}).execute()
    finally:
        client.table("daily_outlook").delete().eq("id", row_id_a).execute()
        client.table("daily_outlook").delete().eq("id", row_id_b).execute()


def test_outcome_label_horizon_minutes_rejects_non_standard_values(client):
    snapshot_id = str(uuid4())
    label_id = str(uuid4())
    snapshot_payload = {
        "id": snapshot_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_date": date.today().isoformat(),
        "config_type": "EXPIRY",
        "dte": 0,
        "raw_readings": {},
        "sub_scores": {},
        "composite_score": 0.0,
        "market_state": "GO",
        "system_status": "OK",
        "reasons": {},
    }
    try:
        client.table("signal_snapshots").insert(snapshot_payload).execute()
        with pytest.raises(APIError):
            client.table("outcome_labels").insert(
                {
                    "id": label_id,
                    "snapshot_id": snapshot_id,
                    "horizon_minutes": 10,  # not in {15, 30, 60}
                    "straddle_price_change": 0.0,
                    "realized_move": 0.0,
                    "direction": "FLAT",
                }
            ).execute()
    finally:
        client.table("outcome_labels").delete().eq("id", label_id).execute()
        client.table("signal_snapshots").delete().eq("id", snapshot_id).execute()


def test_outcome_label_snapshot_delete_sets_null_not_cascade(client):
    snapshot_id = str(uuid4())
    label_id = str(uuid4())
    snapshot_payload = {
        "id": snapshot_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_date": date.today().isoformat(),
        "config_type": "EXPIRY",
        "dte": 0,
        "raw_readings": {},
        "sub_scores": {},
        "composite_score": 0.0,
        "market_state": "GO",
        "system_status": "OK",
        "reasons": {},
    }
    label_payload = {
        "id": label_id,
        "snapshot_id": snapshot_id,
        "horizon_minutes": 15,
        "straddle_price_change": 2.0,
        "realized_move": 0.5,
        "direction": "UP",
    }
    try:
        client.table("signal_snapshots").insert(snapshot_payload).execute()
        client.table("outcome_labels").insert(label_payload).execute()

        client.table("signal_snapshots").delete().eq("id", snapshot_id).execute()

        resp = client.table("outcome_labels").select("*").eq("id", label_id).execute()
        assert resp.data[0]["snapshot_id"] is None
    finally:
        client.table("outcome_labels").delete().eq("id", label_id).execute()
        client.table("signal_snapshots").delete().eq("id", snapshot_id).execute()


def test_outcome_label_no_source_is_db_level_permitted_but_app_level_blocked(client):
    """The outcome_labels_has_source CHECK was dropped in 0006 (conflicted with
    ON DELETE SET NULL, see that migration). The DB now permits a row with both
    sources null; the "at least one source" invariant lives solely in the
    OutcomeLabel pydantic model (tests/storage/test_models.py), which is the
    only code path TASK-012 uses to write this table."""
    label_id = str(uuid4())
    try:
        client.table("outcome_labels").insert(
            {
                "id": label_id,
                "snapshot_id": None,
                "transition_id": None,
                "horizon_minutes": 15,
                "straddle_price_change": 0.0,
                "realized_move": 0.0,
                "direction": "FLAT",
            }
        ).execute()
    finally:
        client.table("outcome_labels").delete().eq("id", label_id).execute()
