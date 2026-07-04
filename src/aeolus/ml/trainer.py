"""ModelTrainer (TASK-017 ADR) -- end-of-day batch retrain, no online
learning. Two independent models per config_type; a failure in one never
blocks the other (expiry data accrues far slower than non-expiry -- one
expiry day/week vs. daily). Called only from TASK-021's EOD hook, strictly
after FeatureStore.sync_eod.

Score-sign convention (binding for TASK-018/019): `IsolationForest.score_samples`
returns lower-is-more-abnormal; this module negates it so scores stored here
and in ml_anomaly_scores are HIGHER = MORE ANOMALOUS, matching the "flag top
5% / clear top 10%" language throughout the ADR/spec.
"""

from __future__ import annotations

import base64
import io
from datetime import datetime, timezone
from typing import Any, Literal, cast

import joblib
import numpy as np
import sklearn
from pydantic import BaseModel
from sklearn.ensemble import IsolationForest
from supabase import Client, create_client

from aeolus.storage.models import ConfigType

from .config import MLTuning
from .features import FEATURE_ORDER, FEATURE_SET_VERSION, SIGMA_FLOOR, Scaler
from .models import MLModelVersion
from .store import FeatureStore

CONFIG_TYPES: tuple[ConfigType, ...] = ("EXPIRY", "NON_EXPIRY")


class TrainResult(BaseModel):
    config_type: ConfigType
    outcome: Literal["TRAINED", "WARMING_UP", "FAILED"]
    version: int | None = None
    sample_count: int
    trading_day_count: int


class ModelTrainer:
    def __init__(
        self,
        store: FeatureStore,
        supabase_url: str,
        supabase_key: str,
        *,
        tuning: MLTuning | None = None,
        client: Client | None = None,
    ) -> None:
        self._store = store
        self._tuning = tuning or MLTuning()
        self._client: Client = client or create_client(supabase_url, supabase_key)

    def train_all(self) -> list[TrainResult]:
        """Both configs, independent; exceptions per config are caught into
        outcome=FAILED. Called only from TASK-021's EOD hook, strictly after
        FeatureStore.sync_eod."""
        results = []
        for config_type in CONFIG_TYPES:
            try:
                results.append(self.train(config_type))
            except Exception:  # noqa: BLE001 -- one config's failure must never block the other
                results.append(
                    TrainResult(config_type=config_type, outcome="FAILED", sample_count=0, trading_day_count=0)
                )
        return results

    def train(self, config_type: ConfigType) -> TrainResult:
        rows = self._store.load_window(config_type, self._tuning.window_days)
        rows = [row for row in rows if all(name in row.raw_values for name in FEATURE_ORDER)]

        sample_count = len(rows)
        trading_day_count = len({row.session_date for row in rows})
        n_features = len(FEATURE_ORDER)

        warm = (
            sample_count >= self._tuning.warmup_min_samples_factor * n_features
            and trading_day_count >= self._tuning.warmup_min_days
        )
        if not warm:
            return TrainResult(
                config_type=config_type,
                outcome="WARMING_UP",
                sample_count=sample_count,
                trading_day_count=trading_day_count,
            )

        matrix = np.array([[row.raw_values[name] for name in FEATURE_ORDER] for row in rows])
        mean = matrix.mean(axis=0)
        std = np.where(matrix.std(axis=0) < SIGMA_FLOOR, SIGMA_FLOOR, matrix.std(axis=0))
        scaler = Scaler(
            mean=dict(zip(FEATURE_ORDER, mean.tolist(), strict=True)),
            std=dict(zip(FEATURE_ORDER, std.tolist(), strict=True)),
        )
        standardized = (matrix - mean) / std

        model = IsolationForest(
            n_estimators=self._tuning.n_estimators,
            max_samples="auto",
            random_state=self._tuning.random_state,
        )
        model.fit(standardized)

        scores = -model.score_samples(standardized)  # see module docstring: higher = more anomalous
        flag_threshold = float(np.quantile(scores, 1 - self._tuning.flag_pct))
        clear_threshold = float(np.quantile(scores, 1 - self._tuning.clear_pct))

        buffer = io.BytesIO()
        joblib.dump(model, buffer)
        model_blob = base64.b64encode(buffer.getvalue()).decode("ascii")
        window_start = min(row.session_date for row in rows)
        window_end = max(row.session_date for row in rows)
        version = self._next_version(config_type)

        registry_row = MLModelVersion(
            config_type=config_type,
            version=version,
            feature_set_version=FEATURE_SET_VERSION,
            model_blob=model_blob,
            sklearn_version=sklearn.__version__,
            scaler_mean=scaler.mean,
            scaler_std=scaler.std,
            flag_threshold=flag_threshold,
            clear_threshold=clear_threshold,
            window_start=window_start,
            window_end=window_end,
            sample_count=sample_count,
            trading_day_count=trading_day_count,
            trained_at=datetime.now(timezone.utc),
        )
        self._client.table(MLModelVersion.TABLE).insert(registry_row.model_dump(mode="json")).execute()

        return TrainResult(
            config_type=config_type,
            outcome="TRAINED",
            version=version,
            sample_count=sample_count,
            trading_day_count=trading_day_count,
        )

    def _next_version(self, config_type: ConfigType) -> int:
        rows = cast(
            "list[dict[str, Any]]",
            self._client.table(MLModelVersion.TABLE)
            .select("version")
            .eq("config_type", config_type)
            .order("version", desc=True)
            .limit(1)
            .execute()
            .data,
        )
        return (rows[0]["version"] + 1) if rows else 1
