"""FeatureStore (TASK-016 ADR) -- persists scored cycles into ml_feature_store,
the ML module's own permanent copy of training data. Live append() plus an
idempotent sync_eod() catch-up share one upsert primitive keyed on the
UNIQUE(source_snapshot_id) constraint (migration 0009), so a row written live
and re-encountered at EOD is byte-identical and silently skipped -- idempotency
by construction, not careful sequencing. Read-only against signal_snapshots.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast
from uuid import UUID

from supabase import Client, create_client

from aeolus.storage.models import ConfigType, SignalSnapshot

from .features import FEATURE_ORDER, FEATURE_SET_VERSION, Scaler, extract_features, standardize
from .models import MLFeatureRow


class FeatureStore:
    def __init__(self, supabase_url: str, supabase_key: str, *, client: Client | None = None) -> None:
        self._client: Client = client or create_client(supabase_url, supabase_key)

    def append(self, snapshot: SignalSnapshot, scaler: Scaler | None) -> bool:
        """Extract -> upsert. False (and no write) when extraction refuses
        (STALE/DISCONNECTED) or any feature is None. Never raises upward on
        duplicate -- idempotent."""
        vector = extract_features(snapshot)
        if vector is None or any(value is None for value in vector.values()):
            return False
        raw_values = cast("dict[str, float]", vector)

        standardized_values = None
        if scaler is not None:
            standardized_values = dict(zip(FEATURE_ORDER, standardize(raw_values, scaler), strict=True))

        row = MLFeatureRow(
            ts=snapshot.ts,
            session_date=snapshot.session_date,
            config_type=snapshot.config_type,
            source_snapshot_id=snapshot.id,
            feature_set_version=FEATURE_SET_VERSION,
            raw_values=raw_values,
            standardized_values=standardized_values,
        )
        self._client.table(MLFeatureRow.TABLE).upsert(
            row.model_dump(mode="json"), on_conflict="source_snapshot_id", ignore_duplicates=True
        ).execute()
        return True

    def sync_eod(self, session_date: date) -> int:
        """Copy the session's not-yet-persisted snapshot vectors. Returns rows
        written. Re-run => 0, no duplicates. MUST complete before retrain in
        the EOD sequence (enforced by TASK-021's hook ordering)."""
        already_stored = self.stored_snapshot_ids(session_date)
        written = 0
        for snapshot in self._load_snapshots(session_date):
            if snapshot.id in already_stored:
                continue
            if self.append(snapshot, scaler=None):
                written += 1
        return written

    def stored_snapshot_ids(self, session_date: date) -> set[UUID]:
        rows = cast(
            "list[dict[str, Any]]",
            self._client.table(MLFeatureRow.TABLE)
            .select("source_snapshot_id")
            .eq("session_date", session_date.isoformat())
            .execute()
            .data,
        )
        return {UUID(row["source_snapshot_id"]) for row in rows}

    def load_window(self, config_type: ConfigType, window_days: int) -> list[MLFeatureRow]:
        """Trainer's read path: most recent `window_days` DISTINCT session_dates
        for config_type, complete rows only (guaranteed -- append/sync_eod
        never write an incomplete row)."""
        date_rows = cast(
            "list[dict[str, Any]]",
            self._client.table(MLFeatureRow.TABLE)
            .select("session_date")
            .eq("config_type", config_type)
            .order("session_date", desc=True)
            .execute()
            .data,
        )
        distinct_dates: list[str] = []
        seen: set[str] = set()
        for row in date_rows:
            session_date = row["session_date"]
            if session_date not in seen:
                seen.add(session_date)
                distinct_dates.append(session_date)
            if len(distinct_dates) == window_days:
                break
        if not distinct_dates:
            return []

        rows = cast(
            "list[dict[str, Any]]",
            self._client.table(MLFeatureRow.TABLE)
            .select("*")
            .eq("config_type", config_type)
            .in_("session_date", distinct_dates)
            .execute()
            .data,
        )
        return [MLFeatureRow(**row) for row in rows]

    def _load_snapshots(self, session_date: date) -> list[SignalSnapshot]:
        rows = cast(
            "list[dict[str, Any]]",
            self._client.table(SignalSnapshot.TABLE)
            .select("*")
            .eq("session_date", session_date.isoformat())
            .order("ts")
            .execute()
            .data,
        )
        return [SignalSnapshot(**row) for row in rows]
