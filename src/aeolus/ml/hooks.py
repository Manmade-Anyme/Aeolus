"""MLHooks facade (TASK-021 ADR) -- wires TASK-016..020's collaborators into
the scheduler as a strict, failure-isolated consumer. Every public method is
total (never raises): each internal step is individually try/except-wrapped,
so an append failure never stops scoring, a scoring failure never stops the
next cycle, and nothing here can ever break the engine loop or session
teardown. This is the only module that composes aeolus.ml's pieces together
-- the engine and scheduler stay ML-ignorant (scheduler.py references this
class only through a structural Protocol, never a runtime import).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, cast
from uuid import UUID

from supabase import Client, create_client

from aeolus.storage.models import ConfigType, SignalSnapshot

from .config import MLTuning
from .explain import anomaly_reason, clear_reason, top_contributors
from .models import MLModelVersion
from .output import MLDiscordDispatcher
from .scorer import LiveScorer, ScoreEvent
from .store import FeatureStore
from .trainer import ModelTrainer

logger = logging.getLogger("aeolus.ml.hooks")

CONFIG_TYPES: tuple[ConfigType, ...] = ("EXPIRY", "NON_EXPIRY")


class MLHooks:
    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        *,
        tuning: MLTuning | None = None,
        store: FeatureStore | None = None,
        scorer: LiveScorer | None = None,
        trainer: ModelTrainer | None = None,
        dispatcher: MLDiscordDispatcher | None = None,
        client: Client | None = None,
    ) -> None:
        self._tuning = tuning or MLTuning()
        self._client: Client = client or create_client(supabase_url, supabase_key)
        self._store = store or FeatureStore(supabase_url, supabase_key, client=self._client)
        self._scorer = scorer or LiveScorer(supabase_url, supabase_key, tuning=self._tuning, client=self._client)
        self._trainer = trainer or ModelTrainer(
            self._store, supabase_url, supabase_key, tuning=self._tuning, client=self._client
        )
        self._dispatcher = dispatcher or MLDiscordDispatcher(self._tuning.ml_discord_webhook_url)

    def start_session(self, session_date: date) -> None:
        """Reset scorer state; detect + announce go-live/warm-up transitions
        observed at session start. Never raises."""
        try:
            self._scorer.start_session(session_date)
        except Exception:
            logger.exception("scorer.start_session failed for %s", session_date)

        for config_type in CONFIG_TYPES:
            try:
                self._announce_progress(config_type)
            except Exception:
                logger.exception("go-live/warm-up announcement failed for config_type=%s", config_type)

    def on_cycle(self, snapshot: SignalSnapshot) -> None:
        """append -> score -> (on a state transition) explain -> post. Never
        raises: each step is independently contained so one failing never
        skips the others."""
        try:
            self._store.append(snapshot, scaler=None)
        except Exception:
            logger.exception("feature store append failed for config_type=%s", snapshot.config_type)

        event: ScoreEvent | None = None
        try:
            event = self._scorer.score_cycle(snapshot)
        except Exception:
            logger.exception("scorer.score_cycle failed for config_type=%s", snapshot.config_type)

        if event is None:
            return

        try:
            self._explain_and_post(event, snapshot)
        except Exception:
            logger.exception("explain/post failed for config_type=%s", snapshot.config_type)

    def on_end_of_day(self, session_date: date) -> None:
        """sync_eod -> train_all, strict order (sync must complete first: it
        is retrain's only data source). Never raises."""
        try:
            self._store.sync_eod(session_date)
        except Exception:
            logger.exception("sync_eod failed for %s", session_date)

        try:
            self._trainer.train_all()
        except Exception:
            logger.exception("train_all failed for %s", session_date)

    def _explain_and_post(self, event: ScoreEvent, snapshot: SignalSnapshot) -> None:
        row = self._registry_row(event.model_version_id)
        if row is None:
            logger.warning(
                "registry row missing for model_version_id=%s; skipping explain/post", event.model_version_id
            )
            return

        contributors = top_contributors(event.z_by_feature)
        if event.kind == "ANOMALY_ENTER":
            reason = anomaly_reason(contributors, event.score, row["flag_threshold"], row["version"])
            self._dispatcher.post_anomaly(event, reason, snapshot)
        else:
            reason = clear_reason(event.score, row["clear_threshold"], row["version"])
            self._dispatcher.post_clear(event, reason, snapshot.config_type)

    def _registry_row(self, model_version_id: UUID) -> dict[str, Any] | None:
        rows = cast(
            "list[dict[str, Any]]",
            self._client.table(MLModelVersion.TABLE)
            .select("version, flag_threshold, clear_threshold")
            .eq("id", str(model_version_id))
            .limit(1)
            .execute()
            .data,
        )
        return rows[0] if rows else None

    def _announce_progress(self, config_type: ConfigType) -> None:
        latest_version = self._latest_version(config_type)
        if latest_version is None:
            rows = self._store.load_window(config_type, self._tuning.window_days)
            day_count = len({row.session_date for row in rows})
            if day_count > 0:
                self._dispatcher.post_warmup_progress(config_type, day=day_count, target=self._tuning.warmup_min_days)
        elif latest_version == 1:
            # version strictly increments from 1 (ModelTrainer._next_version) and
            # retrain is daily, so "version == 1 observed at a fresh session start"
            # unambiguously means this config just left warm-up for the first
            # time -- no extra persisted "already announced" marker needed.
            self._dispatcher.post_golive(config_type, model_version=1)

    def _latest_version(self, config_type: ConfigType) -> int | None:
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
        return rows[0]["version"] if rows else None
