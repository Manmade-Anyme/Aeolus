"""Pydantic models mirroring the three ml_* tables (TASK-014 ADR).

Deliberately separate from storage/models.py — no engine-side file ever
imports aeolus.ml. The ML overlay is advisory-only and reads engine tables
but never writes them; ML_PROTECTED_TABLES documents this module's own
non-negotiable retention exception (see jobs/retention.py PROTECTED_TABLES
for the full engine+ML denylist).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from aeolus.storage.models import ConfigType

ML_PROTECTED_TABLES: frozenset[str] = frozenset({"ml_feature_store"})

MLStatus = Literal["ACTIVE", "WARMING_UP"]


class MLFeatureRow(BaseModel):
    """Permanent training corpus row. Written by TASK-016 sync, one per
    signal_snapshots row, keyed idempotently on source_snapshot_id."""

    TABLE: ClassVar[str] = "ml_feature_store"

    id: UUID = Field(default_factory=uuid4)
    ts: datetime
    session_date: date
    config_type: ConfigType
    source_snapshot_id: UUID
    feature_set_version: int
    raw_values: dict[str, float]
    standardized_values: dict[str, float] | None = None


class MLModelVersion(BaseModel):
    """Registry row for one trained Isolation Forest. Written by TASK-017."""

    TABLE: ClassVar[str] = "ml_model_registry"

    id: UUID = Field(default_factory=uuid4)
    config_type: ConfigType
    version: int
    feature_set_version: int
    model_blob: str
    sklearn_version: str
    scaler_mean: dict[str, float]
    scaler_std: dict[str, float]
    flag_threshold: float
    clear_threshold: float
    window_start: date
    window_end: date
    sample_count: int
    trading_day_count: int
    trained_at: datetime


class MLAnomalyScore(BaseModel):
    """Advisory live-scoring log row. Written by TASK-018."""

    TABLE: ClassVar[str] = "ml_anomaly_scores"

    id: UUID = Field(default_factory=uuid4)
    ts: datetime
    session_date: date
    config_type: ConfigType
    source_snapshot_id: UUID
    score: float
    flagged: bool
    ml_status: MLStatus
    top_features: list[dict[str, float | str]] | None = None
    model_version_id: UUID | None = None
