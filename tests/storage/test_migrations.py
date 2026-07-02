"""DB-level integration tests for TASK-001 migrations. Require a real Postgres
instance (local Supabase via `supabase start`, or any Postgres 15+) reachable
at $TEST_DATABASE_URL. Skipped entirely when that env var is unset — these
exercise actual DDL, not a mock, so they cannot run against nothing.

    supabase start   # or: docker run -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:15
    export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:54322/postgres
    pytest tests/storage/test_migrations.py
"""

import os
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL not set — see module docstring"
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def _apply_migrations(conn):
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        conn.execute(path.read_text())
    conn.commit()


@pytest.fixture
def conn():
    connection = psycopg.connect(DATABASE_URL, autocommit=False)
    yield connection
    connection.rollback()
    connection.close()


def test_migrations_apply_cleanly_and_are_idempotent(conn):
    _apply_migrations(conn)
    # Re-applying must be a no-op, not an error.
    _apply_migrations(conn)

    tables = conn.execute(
        """
        select table_name from information_schema.tables
        where table_schema = 'public'
          and table_name in
              ('signal_snapshots', 'state_transitions', 'daily_outlook', 'outcome_labels')
        """
    ).fetchall()
    assert {row[0] for row in tables} == {
        "signal_snapshots",
        "state_transitions",
        "daily_outlook",
        "outcome_labels",
    }


def test_market_state_and_system_status_are_distinct_enum_types(conn):
    _apply_migrations(conn)
    with pytest.raises(psycopg.errors.DatatypeMismatch):
        conn.execute("select 'GO'::market_state = 'OK'::system_status")
    conn.rollback()


def test_signal_snapshot_round_trip_recomputable(conn):
    _apply_migrations(conn)
    snapshot_id = uuid4()
    conn.execute(
        """
        insert into signal_snapshots
            (id, ts, session_date, config_type, dte, raw_readings, sub_scores,
             composite_score, market_state, system_status, reasons)
        values (%s, now(), current_date, 'EXPIRY', 0, %s, %s, 0.52, 'GO', 'OK', %s)
        """,
        (
            snapshot_id,
            psycopg.types.json.Jsonb({"gamma": {"raw_value": 0.85, "reference_band": [0.5, 1.2]}}),
            psycopg.types.json.Jsonb({"gamma": 0.4}),
            psycopg.types.json.Jsonb({"gamma": "0.85 within [0.5, 1.2] -> 0.40"}),
        ),
    )
    row = conn.execute(
        "select raw_readings, sub_scores from signal_snapshots where id = %s",
        (snapshot_id,),
    ).fetchone()
    assert row[0]["gamma"]["raw_value"] == 0.85
    assert row[1]["gamma"] == 0.4


def test_daily_outlook_trend_exhaustion_flag_is_own_column(conn):
    _apply_migrations(conn)
    columns = conn.execute(
        """
        select column_name, data_type from information_schema.columns
        where table_name = 'daily_outlook' and column_name = 'trend_exhaustion_flag'
        """
    ).fetchall()
    assert columns == [("trend_exhaustion_flag", "boolean")]


def test_outcome_label_snapshot_delete_sets_null_not_cascade(conn):
    _apply_migrations(conn)
    snapshot_id = uuid4()
    conn.execute(
        """
        insert into signal_snapshots
            (id, ts, session_date, config_type, dte, raw_readings, sub_scores,
             composite_score, market_state, system_status, reasons)
        values (%s, now(), current_date, 'EXPIRY', 0, '{}', '{}', 0.0, 'GO', 'OK', '{}')
        """,
        (snapshot_id,),
    )
    label_id = uuid4()
    conn.execute(
        """
        insert into outcome_labels
            (id, snapshot_id, horizon_minutes, straddle_price_change, realized_move, direction)
        values (%s, %s, 15, 2.0, 0.5, 'UP')
        """,
        (label_id, snapshot_id),
    )
    conn.execute("delete from signal_snapshots where id = %s", (snapshot_id,))
    row = conn.execute(
        "select snapshot_id from outcome_labels where id = %s", (label_id,)
    ).fetchone()
    assert row[0] is None
