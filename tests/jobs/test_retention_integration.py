"""Live integration tests for RetentionJob (TASK-014 ADR) -- real Supabase, no
mocking. Far-off dates so these rows never collide with real trading data or
other test suites' fixture dates. Each test cleans up the rows it creates.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from dotenv import dotenv_values

from aeolus.jobs.retention import PROTECTED_TABLES, RetentionJob

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

SESSION_DATE = date(2030, 1, 1)
RETENTION_DAYS = 90
CUTOFF = datetime.combine(SESSION_DATE, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=RETENTION_DAYS)
IN_WINDOW_TS = CUTOFF + timedelta(days=30)
OUT_OF_WINDOW_TS = CUTOFF - timedelta(days=30)


@pytest.fixture
def client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


@pytest.fixture
def job(client):
    return RetentionJob(SUPABASE_URL, SUPABASE_KEY, retention_days=RETENTION_DAYS, client=client)


def _snapshot_row(row_id: str, ts: datetime) -> dict:
    return {
        "id": row_id,
        "ts": ts.isoformat(),
        "session_date": ts.date().isoformat(),
        "config_type": "NON_EXPIRY",
        "dte": 3,
        "raw_readings": {},
        "sub_scores": {},
        "composite_score": 0.0,
        "market_state": "PREPARE",
        "system_status": "OK",
        "reasons": {},
    }


def _score_row(row_id: str, ts: datetime) -> dict:
    return {
        "id": row_id,
        "ts": ts.isoformat(),
        "session_date": ts.date().isoformat(),
        "config_type": "NON_EXPIRY",
        "source_snapshot_id": str(uuid4()),
        "score": 0.1,
        "flagged": False,
        "ml_status": "ACTIVE",
    }


def _feature_row(row_id: str, ts: datetime) -> dict:
    return {
        "id": row_id,
        "ts": ts.isoformat(),
        "session_date": ts.date().isoformat(),
        "config_type": "NON_EXPIRY",
        "source_snapshot_id": str(uuid4()),
        "feature_set_version": 1,
        "raw_values": {"gamma_z": 0.1},
    }


def _transition_row(row_id: str, ts: datetime) -> dict:
    return {
        "id": row_id,
        "ts": ts.isoformat(),
        "from_state": "NO_GO",
        "to_state": "PREPARE",
        "trigger_categories": ["gamma"],
        "reason": "retention test fixture",
    }


def _outlook_row(row_id: str, session_date: date) -> dict:
    return {
        "id": row_id,
        "session_date": session_date.isoformat(),
        "predicted_archetype": "pinned_range",
        "archetype_confidence": 0.5,
        "contributing_inputs": {},
        "trend_exhaustion_flag": False,
        "straddle_level_vs_history": 1.0,
    }


def _label_row(row_id: str) -> dict:
    return {
        "id": row_id,
        "snapshot_id": None,
        "transition_id": None,
        "horizon_minutes": 15,
        "straddle_price_change": 0.0,
        "realized_move": 0.0,
        "direction": "FLAT",
    }


@pytest.fixture
def seeded_rows(client):
    ids = {
        "signal_snapshots": [str(uuid4()), str(uuid4())],
        "ml_anomaly_scores": [str(uuid4()), str(uuid4())],
        "ml_feature_store": [str(uuid4()), str(uuid4())],
        "state_transitions": [str(uuid4()), str(uuid4())],
        "daily_outlook": [str(uuid4()), str(uuid4())],
        "outcome_labels": [str(uuid4()), str(uuid4())],
    }
    client.table("signal_snapshots").insert(
        [
            _snapshot_row(ids["signal_snapshots"][0], IN_WINDOW_TS),
            _snapshot_row(ids["signal_snapshots"][1], OUT_OF_WINDOW_TS),
        ]
    ).execute()
    client.table("ml_anomaly_scores").insert(
        [
            _score_row(ids["ml_anomaly_scores"][0], IN_WINDOW_TS),
            _score_row(ids["ml_anomaly_scores"][1], OUT_OF_WINDOW_TS),
        ]
    ).execute()
    client.table("ml_feature_store").insert(
        [
            _feature_row(ids["ml_feature_store"][0], IN_WINDOW_TS),
            _feature_row(ids["ml_feature_store"][1], OUT_OF_WINDOW_TS),
        ]
    ).execute()
    client.table("state_transitions").insert(
        [
            _transition_row(ids["state_transitions"][0], IN_WINDOW_TS),
            _transition_row(ids["state_transitions"][1], OUT_OF_WINDOW_TS),
        ]
    ).execute()
    client.table("daily_outlook").insert(
        [
            _outlook_row(ids["daily_outlook"][0], IN_WINDOW_TS.date()),
            _outlook_row(ids["daily_outlook"][1], OUT_OF_WINDOW_TS.date()),
        ]
    ).execute()
    client.table("outcome_labels").insert(
        [_label_row(ids["outcome_labels"][0]), _label_row(ids["outcome_labels"][1])]
    ).execute()

    yield ids

    client.table("signal_snapshots").delete().in_("id", ids["signal_snapshots"]).execute()
    client.table("ml_anomaly_scores").delete().in_("id", ids["ml_anomaly_scores"]).execute()
    client.table("ml_feature_store").delete().in_("id", ids["ml_feature_store"]).execute()
    client.table("state_transitions").delete().in_("id", ids["state_transitions"]).execute()
    client.table("daily_outlook").delete().in_("id", ids["daily_outlook"]).execute()
    client.table("outcome_labels").delete().in_("id", ids["outcome_labels"]).execute()


def test_run_trims_out_of_window_and_protects_denylist(client, job, seeded_rows):
    report = job.run(SESSION_DATE)

    assert report.errors == []
    assert report.deleted_counts["signal_snapshots"] >= 1
    assert report.deleted_counts["ml_anomaly_scores"] >= 1

    remaining_snapshots = (
        client.table("signal_snapshots").select("id").in_("id", seeded_rows["signal_snapshots"]).execute().data
    )
    assert [row["id"] for row in remaining_snapshots] == [seeded_rows["signal_snapshots"][0]]

    remaining_scores = (
        client.table("ml_anomaly_scores").select("id").in_("id", seeded_rows["ml_anomaly_scores"]).execute().data
    )
    assert [row["id"] for row in remaining_scores] == [seeded_rows["ml_anomaly_scores"][0]]

    for table in PROTECTED_TABLES:
        remaining = client.table(table).select("id").in_("id", seeded_rows[table]).execute().data
        assert len(remaining) == 2, f"{table} is protected, must be untouched"


def test_run_second_pass_deletes_nothing_further(job, seeded_rows):
    job.run(SESSION_DATE)
    second_report = job.run(SESSION_DATE)

    assert second_report.errors == []
    assert second_report.deleted_counts["signal_snapshots"] == 0
    assert second_report.deleted_counts["ml_anomaly_scores"] == 0


@pytest.fixture
def seeded_registry(client):
    config_type = "EXPIRY"
    version_base = 900_000  # far from any real trained version
    ids = [str(uuid4()) for _ in range(35)]
    rows = [
        {
            "id": row_id,
            "config_type": config_type,
            "version": version_base + i,
            "feature_set_version": 1,
            "model_blob": "stub",
            "sklearn_version": "1.5.0",
            "scaler_mean": {},
            "scaler_std": {},
            "flag_threshold": 0.65,
            "clear_threshold": 0.55,
            "window_start": date(2026, 1, 1).isoformat(),
            "window_end": date(2026, 1, 31).isoformat(),
            "sample_count": 100,
            "trading_day_count": 20,
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }
        for i, row_id in enumerate(ids)
    ]
    client.table("ml_model_registry").insert(rows).execute()

    yield config_type, ids, version_base

    client.table("ml_model_registry").delete().in_("id", ids).execute()


def test_prune_registry_keeps_newest_n_never_below_one(client, seeded_registry):
    config_type, ids, version_base = seeded_registry
    job = RetentionJob(
        SUPABASE_URL, SUPABASE_KEY, retention_days=RETENTION_DAYS, registry_keep_versions=30, client=client
    )

    report = job.run(SESSION_DATE)

    assert report.pruned_registry_versions == 5
    remaining = (
        client.table("ml_model_registry")
        .select("version")
        .in_("id", ids)
        .order("version", desc=True)
        .execute()
        .data
    )
    assert len(remaining) == 30
    versions = {row["version"] for row in remaining}
    assert versions == {version_base + i for i in range(5, 35)}
