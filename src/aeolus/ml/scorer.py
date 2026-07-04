"""LiveScorer (TASK-018 ADR) -- per-cycle Isolation Forest scoring plus the
debounced + hysteresis-gated anomaly state machine. Advisory only: never
writes engine tables, never touches market_state. The scorer never talks to
Discord itself -- it returns a ScoreEvent only on a state transition; the
caller (TASK-021's hook) drives explanation (TASK-019) and posting (TASK-020).

Score-sign convention (set by TASK-017): higher score = more anomalous.
"""

from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, cast
from uuid import UUID

import joblib
import sklearn
from pydantic import BaseModel
from supabase import Client, create_client

from aeolus.storage.models import ConfigType, SignalSnapshot

from .config import MLTuning
from .features import FEATURE_ORDER, FEATURE_SET_VERSION, Scaler, extract_features, standardize
from .models import MLAnomalyScore, MLModelVersion

logger = logging.getLogger("aeolus.ml.scorer")

TOP_FEATURES_COUNT = 3


class ScoreEvent(BaseModel):
    kind: Literal["ANOMALY_ENTER", "ANOMALY_CLEAR"]
    score: float
    z_by_feature: dict[str, float]
    model_version_id: UUID


@dataclass
class AnomalyState:
    flagged: bool = False
    dwell_cycles: int = 0

    def step(
        self, score: float, flag_threshold: float, clear_threshold: float, min_dwell_cycles: int
    ) -> Literal["ANOMALY_ENTER", "ANOMALY_CLEAR"] | None:
        would_enter = not self.flagged and score >= flag_threshold
        would_clear = self.flagged and score < clear_threshold

        if (would_enter or would_clear) and self.dwell_cycles < min_dwell_cycles:
            self.dwell_cycles += 1
            return None

        if would_enter:
            self.flagged = True
            self.dwell_cycles = 0
            return "ANOMALY_ENTER"
        if would_clear:
            self.flagged = False
            self.dwell_cycles = 0
            return "ANOMALY_CLEAR"

        self.dwell_cycles += 1
        return None


@dataclass
class _CachedModel:
    registry_id: UUID
    model: Any
    scaler: Scaler
    flag_threshold: float
    clear_threshold: float
    cached_at: float


def _top_features(z_by_feature: dict[str, float], count: int) -> list[dict[str, float | str]]:
    ranked = sorted(z_by_feature.items(), key=lambda pair: abs(pair[1]), reverse=True)
    return [{"name": name, "z": z} for name, z in ranked[:count]]


class LiveScorer:
    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        *,
        tuning: MLTuning | None = None,
        client: Client | None = None,
    ) -> None:
        self._tuning = tuning or MLTuning()
        self._client: Client = client or create_client(supabase_url, supabase_key)
        self._model_cache: dict[ConfigType, _CachedModel] = {}
        self._states: dict[ConfigType, AnomalyState] = {}

    def start_session(self, session_date: date) -> None:
        """Session-scoped reset of the debounce/hysteresis state machine.
        Model cache is NOT cleared here -- it's governed purely by its own
        TTL (a fresh process, TASK-013's one-process-per-session model, has
        an empty cache anyway; a long-lived instance reused across sessions
        in tests picks up a retrain naturally once the TTL lapses)."""
        self._states = {}

    def score_cycle(self, snapshot: SignalSnapshot) -> ScoreEvent | None:
        """Full live path: refuse/extract/standardize/score/persist/step state
        machine. Returns an event ONLY on a state transition; None otherwise
        (including every cycle of an ongoing anomaly -- debounce lives here).
        Never raises: internal errors are caught, logged, -> None."""
        try:
            return self._score_cycle(snapshot)
        except Exception:
            logger.exception("score_cycle failed for config_type=%s", snapshot.config_type)
            return None

    def _score_cycle(self, snapshot: SignalSnapshot) -> ScoreEvent | None:
        cached = self._load_latest_model(snapshot.config_type)
        if cached is None:
            return None

        vector = extract_features(snapshot)
        if vector is None or any(value is None for value in vector.values()):
            return None
        raw_values = cast("dict[str, float]", vector)

        z = standardize(raw_values, cached.scaler)
        z_by_feature = dict(zip(FEATURE_ORDER, z, strict=True))
        score = float(-cached.model.score_samples([z])[0])

        state = self._states.setdefault(snapshot.config_type, AnomalyState())
        kind = state.step(score, cached.flag_threshold, cached.clear_threshold, self._tuning.min_dwell_cycles)

        top_features = _top_features(z_by_feature, TOP_FEATURES_COUNT) if kind is not None else None
        self._write_score_row(snapshot, score, state.flagged, cached.registry_id, top_features)

        if kind is None:
            return None
        return ScoreEvent(kind=kind, score=score, z_by_feature=z_by_feature, model_version_id=cached.registry_id)

    def _load_latest_model(self, config_type: ConfigType) -> _CachedModel | None:
        cached = self._model_cache.get(config_type)
        now = time.monotonic()
        if cached is not None and (now - cached.cached_at) < self._tuning.model_cache_ttl_seconds:
            return cached

        try:
            rows = cast(
                "list[dict[str, Any]]",
                self._client.table(MLModelVersion.TABLE)
                .select("*")
                .eq("config_type", config_type)
                .order("version", desc=True)
                .limit(1)
                .execute()
                .data,
            )
        except Exception:
            logger.exception("registry unreachable for config_type=%s; keeping cached model", config_type)
            return cached

        if not rows:
            self._model_cache.pop(config_type, None)
            return None

        row = rows[0]
        stored_sklearn_major_minor = ".".join(str(row["sklearn_version"]).split(".")[:2])
        runtime_sklearn_major_minor = ".".join(sklearn.__version__.split(".")[:2])
        if stored_sklearn_major_minor != runtime_sklearn_major_minor or row["feature_set_version"] != FEATURE_SET_VERSION:
            logger.warning(
                "model version mismatch for config_type=%s (sklearn %s vs runtime %s, "
                "feature_set_version %s vs %s) -- treating as no-model (WARMING_UP)",
                config_type,
                row["sklearn_version"],
                sklearn.__version__,
                row["feature_set_version"],
                FEATURE_SET_VERSION,
            )
            self._model_cache.pop(config_type, None)
            return None

        model = joblib.load(io.BytesIO(base64.b64decode(row["model_blob"])))
        scaler = Scaler(mean=row["scaler_mean"], std=row["scaler_std"])
        entry = _CachedModel(
            registry_id=UUID(row["id"]),
            model=model,
            scaler=scaler,
            flag_threshold=row["flag_threshold"],
            clear_threshold=row["clear_threshold"],
            cached_at=now,
        )
        self._model_cache[config_type] = entry
        return entry

    def _write_score_row(
        self,
        snapshot: SignalSnapshot,
        score: float,
        flagged: bool,
        model_version_id: UUID,
        top_features: list[dict[str, float | str]] | None,
    ) -> None:
        row = MLAnomalyScore(
            ts=snapshot.ts,
            session_date=snapshot.session_date,
            config_type=snapshot.config_type,
            source_snapshot_id=snapshot.id,
            score=score,
            flagged=flagged,
            ml_status="ACTIVE",
            top_features=top_features,
            model_version_id=model_version_id,
        )
        self._client.table(MLAnomalyScore.TABLE).insert(row.model_dump(mode="json")).execute()
