"""Contract tests for TASK-014 ml models. No internal mocking -- exercises the
public pydantic contract directly (construction, JSON round-trip, validation)."""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aeolus.ml.models import ML_PROTECTED_TABLES, MLAnomalyScore, MLFeatureRow, MLModelVersion


def test_ml_feature_row_round_trip():
    row = MLFeatureRow(
        ts=datetime(2026, 7, 3, 9, 20, tzinfo=timezone.utc),
        session_date=date(2026, 7, 3),
        config_type="EXPIRY",
        source_snapshot_id=uuid4(),
        feature_set_version=1,
        raw_values={"gamma_z": 0.85},
        standardized_values={"gamma_z": 1.2},
    )
    restored = MLFeatureRow.model_validate(row.model_dump(mode="json"))
    assert restored.raw_values == row.raw_values
    assert restored.standardized_values == row.standardized_values


def test_ml_feature_row_standardized_values_optional():
    row = MLFeatureRow(
        ts=datetime.now(timezone.utc),
        session_date=date(2026, 7, 3),
        config_type="NON_EXPIRY",
        source_snapshot_id=uuid4(),
        feature_set_version=1,
        raw_values={"gamma_z": 0.5},
    )
    assert row.standardized_values is None


def test_ml_model_version_round_trip():
    version = MLModelVersion(
        config_type="EXPIRY",
        version=1,
        feature_set_version=1,
        model_blob="base64-blob",
        sklearn_version="1.5.0",
        scaler_mean={"gamma_z": 0.1},
        scaler_std={"gamma_z": 0.9},
        flag_threshold=0.65,
        clear_threshold=0.55,
        window_start=date(2026, 6, 1),
        window_end=date(2026, 6, 30),
        sample_count=1000,
        trading_day_count=21,
        trained_at=datetime.now(timezone.utc),
    )
    restored = MLModelVersion.model_validate(version.model_dump(mode="json"))
    assert restored.flag_threshold == version.flag_threshold
    assert restored.clear_threshold == version.clear_threshold


def test_ml_anomaly_score_top_features_none_when_not_flagged():
    score = MLAnomalyScore(
        ts=datetime.now(timezone.utc),
        session_date=date(2026, 7, 3),
        config_type="EXPIRY",
        source_snapshot_id=uuid4(),
        score=0.1,
        flagged=False,
        ml_status="ACTIVE",
    )
    assert score.top_features is None
    assert score.model_version_id is None


def test_ml_anomaly_score_rejects_invalid_status():
    with pytest.raises(ValidationError):
        MLAnomalyScore(
            ts=datetime.now(timezone.utc),
            session_date=date(2026, 7, 3),
            config_type="EXPIRY",
            source_snapshot_id=uuid4(),
            score=0.9,
            flagged=True,
            ml_status="BOGUS",
        )


def test_table_names_match_migration_files():
    assert MLFeatureRow.TABLE == "ml_feature_store"
    assert MLModelVersion.TABLE == "ml_model_registry"
    assert MLAnomalyScore.TABLE == "ml_anomaly_scores"


def test_ml_protected_tables_is_feature_store_only():
    assert ML_PROTECTED_TABLES == {"ml_feature_store"}
