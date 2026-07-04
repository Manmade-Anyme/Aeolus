"""Live integration tests for ModelTrainer (TASK-017 ADR) -- real Supabase, no
mocking. Far-off session dates so these rows never collide with real trading
data or other test suites' fixture dates. Each test cleans up the rows it
creates. Synthetic feature vectors (fixed-seed Gaussian noise around a
baseline) stand in for real signal history -- the trainer only cares about
shape/completeness and the resulting statistics, not domain realism.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import numpy as np
import pytest
from dotenv import dotenv_values

from aeolus.ml.config import MLTuning
from aeolus.ml.features import FEATURE_ORDER
from aeolus.ml.store import FeatureStore
from aeolus.ml.trainer import ModelTrainer

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

BASE_DATE = date(2030, 6, 1)
TUNING = MLTuning()  # window_days=30, flag_pct=0.05, clear_pct=0.10, warmup 10x/15d, n_estimators=200


def _seed_rows(config_type: str, day_offset_base: int, num_days: int, rows_per_day: int) -> list[dict]:
    rng = np.random.default_rng(seed=42)
    rows = []
    for day_idx in range(num_days):
        session_date = BASE_DATE + timedelta(days=day_offset_base + day_idx)
        for row_idx in range(rows_per_day):
            raw_values = {name: float(rng.normal(loc=0.0, scale=1.0)) for name in FEATURE_ORDER}
            rows.append(
                {
                    "id": str(uuid4()),
                    "ts": datetime.combine(session_date, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
                    "session_date": session_date.isoformat(),
                    "config_type": config_type,
                    "source_snapshot_id": str(uuid4()),
                    "feature_set_version": 1,
                    "raw_values": raw_values,
                }
            )
    return rows


@pytest.fixture
def client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


@pytest.fixture
def store(client):
    return FeatureStore(SUPABASE_URL, SUPABASE_KEY, client=client)


@pytest.fixture
def trainer(store, client):
    return ModelTrainer(store, SUPABASE_URL, SUPABASE_KEY, tuning=TUNING, client=client)


def _cleanup_feature_rows(client, rows: list[dict]) -> None:
    ids = [row["id"] for row in rows]
    if ids:
        client.table("ml_feature_store").delete().in_("id", ids).execute()


def _cleanup_registry(client, config_type: str) -> None:
    client.table("ml_model_registry").delete().eq("config_type", config_type).execute()


def _safe(cleanup_fn) -> None:
    """One cleanup call failing must never mask a later one in the same
    finally block (a real prior bug -- see TASK-018 debug report)."""
    try:
        cleanup_fn()
    except Exception:  # noqa: BLE001 -- best-effort test cleanup, never re-raise
        pass


def test_train_produces_trained_result_with_valid_threshold_and_round_trip_scores(client, store, trainer):
    import base64
    import io

    import joblib

    rows = _seed_rows("NON_EXPIRY", day_offset_base=0, num_days=16, rows_per_day=12)  # 192 samples, 16 days
    try:
        client.table("ml_feature_store").insert(rows).execute()

        result = trainer.train("NON_EXPIRY")
        assert result.outcome == "TRAINED"
        assert result.version == 1
        assert result.sample_count == 192
        assert result.trading_day_count == 16

        registry = (
            client.table("ml_model_registry")
            .select("*")
            .eq("config_type", "NON_EXPIRY")
            .eq("version", 1)
            .execute()
            .data[0]
        )

        model = joblib.load(io.BytesIO(base64.b64decode(registry["model_blob"])))
        mean = np.array([registry["scaler_mean"][name] for name in FEATURE_ORDER])
        std = np.array([registry["scaler_std"][name] for name in FEATURE_ORDER])
        matrix = np.array([[row["raw_values"][name] for name in FEATURE_ORDER] for row in rows])
        standardized = (matrix - mean) / std

        recomputed_scores = -model.score_samples(standardized)
        expected_flag = float(np.quantile(recomputed_scores, 1 - TUNING.flag_pct))
        expected_clear = float(np.quantile(recomputed_scores, 1 - TUNING.clear_pct))

        assert registry["flag_threshold"] == pytest.approx(expected_flag)
        assert registry["clear_threshold"] == pytest.approx(expected_clear)
        assert registry["flag_threshold"] > registry["clear_threshold"]
    finally:
        _safe(lambda: _cleanup_feature_rows(client, rows))
        _safe(lambda: _cleanup_registry(client, "NON_EXPIRY"))


def test_train_warming_up_insufficient_days_despite_enough_samples(client, store, trainer):
    rows = _seed_rows("EXPIRY", day_offset_base=100, num_days=3, rows_per_day=60)  # 180 samples, only 3 days
    try:
        client.table("ml_feature_store").insert(rows).execute()

        result = trainer.train("EXPIRY")
        assert result.outcome == "WARMING_UP"
        assert result.version is None
        assert result.sample_count == 180
        assert result.trading_day_count == 3

        assert client.table("ml_model_registry").select("id").eq("config_type", "EXPIRY").execute().data == []
    finally:
        _safe(lambda: _cleanup_feature_rows(client, rows))
        _safe(lambda: _cleanup_registry(client, "EXPIRY"))


def test_train_warming_up_insufficient_samples_despite_enough_days(client, store, trainer):
    rows = _seed_rows("EXPIRY", day_offset_base=200, num_days=16, rows_per_day=2)  # 32 samples, 16 days
    try:
        client.table("ml_feature_store").insert(rows).execute()

        result = trainer.train("EXPIRY")
        assert result.outcome == "WARMING_UP"
        assert result.version is None
        assert result.sample_count == 32
        assert result.trading_day_count == 16
    finally:
        _safe(lambda: _cleanup_feature_rows(client, rows))
        _safe(lambda: _cleanup_registry(client, "EXPIRY"))


def test_train_all_two_configs_independent(client, store, trainer):
    non_expiry_rows = _seed_rows("NON_EXPIRY", day_offset_base=300, num_days=16, rows_per_day=12)
    expiry_rows = _seed_rows("EXPIRY", day_offset_base=300, num_days=2, rows_per_day=10)  # far short
    try:
        client.table("ml_feature_store").insert(non_expiry_rows).execute()
        client.table("ml_feature_store").insert(expiry_rows).execute()

        results = {r.config_type: r for r in trainer.train_all()}

        assert results["NON_EXPIRY"].outcome == "TRAINED"
        assert results["EXPIRY"].outcome == "WARMING_UP"
    finally:
        _safe(lambda: _cleanup_feature_rows(client, non_expiry_rows))
        _safe(lambda: _cleanup_feature_rows(client, expiry_rows))
        _safe(lambda: _cleanup_registry(client, "NON_EXPIRY"))
        _safe(lambda: _cleanup_registry(client, "EXPIRY"))


def test_train_version_increments_and_prior_row_untouched(client, store, trainer):
    rows = _seed_rows("NON_EXPIRY", day_offset_base=400, num_days=16, rows_per_day=12)
    try:
        client.table("ml_feature_store").insert(rows).execute()

        first = trainer.train("NON_EXPIRY")
        second = trainer.train("NON_EXPIRY")

        assert first.version == 1
        assert second.version == 2

        first_row = (
            client.table("ml_model_registry")
            .select("flag_threshold")
            .eq("config_type", "NON_EXPIRY")
            .eq("version", 1)
            .execute()
            .data[0]
        )
        second_row = (
            client.table("ml_model_registry")
            .select("flag_threshold")
            .eq("config_type", "NON_EXPIRY")
            .eq("version", 2)
            .execute()
            .data[0]
        )
        # Same seeded data both times -> deterministic fit (fixed random_state) ->
        # identical thresholds; version 1's row is untouched, not overwritten.
        assert first_row["flag_threshold"] == pytest.approx(second_row["flag_threshold"])

        all_versions = (
            client.table("ml_model_registry")
            .select("version")
            .eq("config_type", "NON_EXPIRY")
            .execute()
            .data
        )
        assert {row["version"] for row in all_versions} == {1, 2}
    finally:
        _safe(lambda: _cleanup_feature_rows(client, rows))
        _safe(lambda: _cleanup_registry(client, "NON_EXPIRY"))
