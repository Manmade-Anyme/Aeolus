"""Live integration tests for FeatureStore (TASK-016 ADR) -- real Supabase, no
mocking. Far-off dates so these rows never collide with real trading data or
other test suites' fixture dates. Each test cleans up the rows it creates.
"""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from dotenv import dotenv_values

from aeolus.ml.models import MLFeatureRow
from aeolus.ml.store import FeatureStore
from aeolus.storage.models import SignalSnapshot

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

SESSION_DATE = date(2030, 3, 1)
BASE_TS = datetime(2030, 3, 1, 4, 0, tzinfo=timezone.utc)

_COMPLETE_RAW_READINGS = {
    "volatility": {
        "iv_percentile_rank": {"raw_value": 42.5, "reference_band": [20.0, 80.0], "sub_score": 0.7},
        "vix_level_and_roc": {
            "raw_value": 14.5,
            "reference_band": [10.0, 30.0],
            "sub_score": 0.6,
            "context": {"roc": 1.2},
        },
        "expected_move_consumed_ratio": {"raw_value": 0.4, "reference_band": [0.3, 0.9], "sub_score": 0.3},
    },
    "gamma": {
        "gex_regime": {"raw_value": -15000.0, "reference_band": [0.0, 1.0], "sub_score": 0.65},
        "spot_distance_from_flip": {"raw_value": 1.8, "reference_band": [0.5, 3.0], "sub_score": 0.4},
    },
    "oi_structure": {
        "pcr_level_and_roc": {
            "raw_value": 0.03,
            "reference_band": [0.0, 0.1],
            "sub_score": 0.55,
            "context": {"pcr_level": 0.85},
        },
    },
    "order_flow": {
        "cvd_direction_and_divergence": {"raw_value": 320.0, "reference_band": [50.0, 500.0], "sub_score": 0.75},
    },
    "context": {},
}


def _snapshot_row(row_id: str, ts: datetime, *, config_type: str = "EXPIRY", system_status: str = "OK") -> dict:
    return {
        "id": row_id,
        "ts": ts.isoformat(),
        "session_date": ts.date().isoformat(),
        "config_type": config_type,
        "dte": 0,
        "raw_readings": _COMPLETE_RAW_READINGS,
        "sub_scores": {
            "volatility": 0.6,
            "gamma": 0.55,
            "oi_structure": 0.5,
            "order_flow": 0.75,
            "context": 0.45,
        },
        "composite_score": 0.58,
        "market_state": "PREPARE",
        "system_status": system_status,
        "reasons": {},
    }


@pytest.fixture
def client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


@pytest.fixture
def store(client):
    return FeatureStore(SUPABASE_URL, SUPABASE_KEY, client=client)


def test_append_writes_row_and_ignores_duplicate_on_replay(client, store):
    row_id = str(uuid4())
    snapshot = SignalSnapshot(**_snapshot_row(row_id, BASE_TS))

    try:
        assert store.append(snapshot, scaler=None) is True
        assert store.append(snapshot, scaler=None) is True  # replay: extraction still succeeds, upsert dedupes

        rows = (
            client.table("ml_feature_store").select("*").eq("source_snapshot_id", row_id).execute().data
        )
        assert len(rows) == 1
        assert rows[0]["feature_set_version"] == 1
        assert rows[0]["raw_values"]["composite_score"] == 0.58
    finally:
        client.table("ml_feature_store").delete().eq("source_snapshot_id", row_id).execute()


def test_append_stale_snapshot_never_written(client, store):
    row_id = str(uuid4())
    snapshot = SignalSnapshot(**_snapshot_row(row_id, BASE_TS, system_status="STALE"))

    assert store.append(snapshot, scaler=None) is False
    rows = client.table("ml_feature_store").select("*").eq("source_snapshot_id", row_id).execute().data
    assert rows == []


def test_sync_eod_copies_unstored_snapshots_and_is_idempotent(client, store):
    ok_id, stale_id = str(uuid4()), str(uuid4())
    rows = [
        _snapshot_row(ok_id, BASE_TS),
        _snapshot_row(stale_id, BASE_TS.replace(minute=5), system_status="STALE"),
    ]
    try:
        client.table("signal_snapshots").insert(rows).execute()

        written = store.sync_eod(SESSION_DATE)
        assert written == 1  # only the OK row extracts successfully

        stored = client.table("ml_feature_store").select("source_snapshot_id").eq(
            "session_date", SESSION_DATE.isoformat()
        ).execute().data
        assert {row["source_snapshot_id"] for row in stored} == {ok_id}

        assert store.sync_eod(SESSION_DATE) == 0  # second pass: nothing left to copy
    finally:
        client.table("ml_feature_store").delete().eq("session_date", SESSION_DATE.isoformat()).execute()
        client.table("signal_snapshots").delete().in_("id", [ok_id, stale_id]).execute()


def test_load_window_filters_config_type_and_windows_by_distinct_dates(client, store):
    dates = [date(2030, 4, 1), date(2030, 4, 2), date(2030, 4, 3)]
    expiry_ids = [str(uuid4()) for _ in dates]
    non_expiry_id = str(uuid4())

    feature_rows = [
        MLFeatureRow(
            ts=datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc),
            session_date=d,
            config_type="EXPIRY",
            source_snapshot_id=row_id,
            feature_set_version=1,
            raw_values={"composite_score": 0.5},
        ).model_dump(mode="json")
        for d, row_id in zip(dates, expiry_ids, strict=True)
    ]
    feature_rows.append(
        MLFeatureRow(
            ts=datetime.combine(dates[-1], datetime.min.time(), tzinfo=timezone.utc),
            session_date=dates[-1],
            config_type="NON_EXPIRY",
            source_snapshot_id=non_expiry_id,
            feature_set_version=1,
            raw_values={"composite_score": 0.5},
        ).model_dump(mode="json")
    )
    try:
        client.table("ml_feature_store").insert(feature_rows).execute()

        window = store.load_window("EXPIRY", 2)

        assert {str(row.source_snapshot_id) for row in window} == {expiry_ids[1], expiry_ids[2]}
        assert all(row.config_type == "EXPIRY" for row in window)
    finally:
        client.table("ml_feature_store").delete().in_(
            "source_snapshot_id", [*expiry_ids, non_expiry_id]
        ).execute()
