"""Live integration tests for OutcomeBackfillJob (TASK-012 ADR) -- real
Supabase, no mocking. Far-off session_date so these rows never collide with
real trading data or other test suites' fixture dates. Each test cleans up
the rows it creates.

Requires migration 0007_outcome_labels_idempotency.sql to be applied (the
partial unique indexes this job's upsert-based idempotency depends on).
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from dotenv import dotenv_values

from aeolus.jobs.backfill import OutcomeBackfillJob

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

SESSION_DATE = date(1999, 9, 15)
BASE_TS = datetime(1999, 9, 15, 4, 0, tzinfo=timezone.utc)


def _snapshot_row(minute: int, futures_ltp: float, india_vix: float, gap_raw: float = 0.0) -> dict:
    return {
        "id": str(uuid4()),
        "ts": (BASE_TS + timedelta(minutes=minute)).isoformat(),
        "session_date": SESSION_DATE.isoformat(),
        "config_type": "NON_EXPIRY",
        "dte": 3,
        "raw_readings": {
            "order_flow": {"_carry": {"futures_ltp": futures_ltp}},
            "volatility": {"_carry": {"spot_ltp": futures_ltp, "india_vix": india_vix, "current_iv": india_vix}},
            "context": {"gap_classification": {"raw_value": gap_raw}},
        },
        "sub_scores": {},
        "composite_score": 0.5,
        "market_state": "PREPARE",
        "system_status": "OK",
        "reasons": {},
    }


@pytest.fixture
def client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


@pytest.fixture
def job():
    return OutcomeBackfillJob(SUPABASE_URL, SUPABASE_KEY)


def _cleanup(client, snapshot_ids, transition_ids):
    if snapshot_ids:
        client.table("outcome_labels").delete().in_("snapshot_id", snapshot_ids).execute()
    if transition_ids:
        client.table("outcome_labels").delete().in_("transition_id", transition_ids).execute()
    client.table("signal_snapshots").delete().eq("session_date", SESSION_DATE.isoformat()).execute()
    client.table("state_transitions").delete().gte("ts", BASE_TS.isoformat()).lt(
        "ts", (BASE_TS + timedelta(hours=6)).isoformat()
    ).execute()
    client.table("daily_outlook").delete().eq("session_date", SESSION_DATE.isoformat()).execute()


def test_run_labels_snapshots_and_transitions_and_backfills_archetype(client, job):
    rows = [
        _snapshot_row(0, 100.0, 12.0, gap_raw=0.0),
        _snapshot_row(15, 110.0, 13.0),
        _snapshot_row(30, 118.0, 14.0),
        _snapshot_row(60, 125.0, 15.0),
    ]
    snapshot_ids = [row["id"] for row in rows]
    transition_id = str(uuid4())
    transition_row = {
        "id": transition_id,
        "ts": (BASE_TS + timedelta(minutes=1)).isoformat(),
        "from_state": "PREPARE",
        "to_state": "GO",
        "trigger_categories": ["volatility"],
        "reason": "PREPARE->GO driven by volatility; composite=0.65, confirmed after 3 cycles",
    }
    outlook_row = {
        "id": str(uuid4()),
        "session_date": SESSION_DATE.isoformat(),
        "predicted_archetype": "grinding_trend",
        "archetype_confidence": 0.5,
        "contributing_inputs": {},
        "trend_exhaustion_flag": False,
        "straddle_level_vs_history": 0.5,
    }

    try:
        client.table("signal_snapshots").insert(rows).execute()
        client.table("state_transitions").insert(transition_row).execute()
        client.table("daily_outlook").insert(outlook_row).execute()

        job.run(SESSION_DATE)

        labels = (
            client.table("outcome_labels")
            .select("*")
            .in_("snapshot_id", snapshot_ids)
            .execute()
            .data
        )
        assert len(labels) > 0
        assert all(label["horizon_minutes"] in (15, 30, 60) for label in labels)

        transition_labels = (
            client.table("outcome_labels")
            .select("*")
            .eq("transition_id", transition_id)
            .execute()
            .data
        )
        assert len(transition_labels) > 0

        outlook = (
            client.table("daily_outlook")
            .select("realized_archetype")
            .eq("session_date", SESSION_DATE.isoformat())
            .execute()
            .data
        )
        assert outlook[0]["realized_archetype"] is not None
    finally:
        _cleanup(client, snapshot_ids, [transition_id])


def test_rerun_is_idempotent_no_duplicate_labels(client, job):
    rows = [
        _snapshot_row(0, 100.0, 12.0),
        _snapshot_row(15, 110.0, 13.0),
    ]
    snapshot_ids = [row["id"] for row in rows]

    try:
        client.table("signal_snapshots").insert(rows).execute()

        job.run(SESSION_DATE)
        first_count = len(
            client.table("outcome_labels").select("id").in_("snapshot_id", snapshot_ids).execute().data
        )

        job.run(SESSION_DATE)
        second_count = len(
            client.table("outcome_labels").select("id").in_("snapshot_id", snapshot_ids).execute().data
        )

        assert first_count > 0
        assert first_count == second_count
    finally:
        _cleanup(client, snapshot_ids, [])


def test_missing_daily_outlook_row_does_not_raise(client, job):
    rows = [_snapshot_row(0, 100.0, 12.0), _snapshot_row(15, 110.0, 13.0)]
    snapshot_ids = [row["id"] for row in rows]
    try:
        client.table("signal_snapshots").insert(rows).execute()
        job.run(SESSION_DATE)  # no daily_outlook row exists for SESSION_DATE -- must not raise
    finally:
        _cleanup(client, snapshot_ids, [])
