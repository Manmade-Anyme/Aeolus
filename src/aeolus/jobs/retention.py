"""RetentionJob (TASK-014 ADR) -- engine-side EOD cleanup job. Keeps the
shared Supabase instance bounded (~20-25 MB/day growth at 5s cycles) without
ever touching protected tables. Lives in jobs/, not aeolus.ml, because it
deletes signal_snapshots rows and the ML module is constrained to be
strictly read-only against engine tables. Runs LAST in the EOD sequence
(sync -> retrain -> cleanup, wired by TASK-021).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

from supabase import Client, create_client

from aeolus.storage.models import ConfigType

# The four tables that must NEVER be trimmed: the permanent ML training
# corpus plus the three engine audit logs. Every delete/prune method below
# asserts its target is not in this set before issuing SQL -- protection is
# structural, not conventional (ADR-014).
PROTECTED_TABLES: frozenset[str] = frozenset(
    {"ml_feature_store", "state_transitions", "daily_outlook", "outcome_labels"}
)

AGE_TRIMMED_TABLES: tuple[str, ...] = ("signal_snapshots", "ml_anomaly_scores")
REGISTRY_TABLE = "ml_model_registry"
BATCH_SIZE = 500
CONFIG_TYPES: tuple[ConfigType, ...] = ("EXPIRY", "NON_EXPIRY")


@dataclass
class RetentionReport:
    deleted_counts: dict[str, int] = field(default_factory=dict)
    pruned_registry_versions: int = 0
    errors: list[str] = field(default_factory=list)


class RetentionJob:
    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        *,
        retention_days: int = 90,
        registry_keep_versions: int = 30,
        client: Client | None = None,
    ) -> None:
        self._retention_days = retention_days
        self._registry_keep_versions = registry_keep_versions
        self._client: Client = client or create_client(supabase_url, supabase_key)

    def run(self, session_date: date) -> RetentionReport:
        """Trim signal_snapshots + ml_anomaly_scores older than retention_days;
        prune ml_model_registry beyond registry_keep_versions per config_type.
        Idempotent; batched; never raises upward (log + partial report)."""
        report = RetentionReport()
        cutoff = datetime.combine(session_date, datetime.min.time(), tzinfo=timezone.utc) - timedelta(
            days=self._retention_days
        )

        for table in AGE_TRIMMED_TABLES:
            try:
                report.deleted_counts[table] = self._trim_by_age(table, cutoff)
            except Exception as exc:  # noqa: BLE001 -- partial failure must not abort the run
                report.errors.append(f"{table}: {exc}")

        try:
            report.pruned_registry_versions = self._prune_registry()
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{REGISTRY_TABLE}: {exc}")

        return report

    def _trim_by_age(self, table: str, cutoff: datetime) -> int:
        assert table not in PROTECTED_TABLES, f"{table} is protected, refusing to trim"
        total = 0
        while True:
            rows = cast(
                "list[dict[str, Any]]",
                self._client.table(table)
                .select("id")
                .lt("ts", cutoff.isoformat())
                .limit(BATCH_SIZE)
                .execute()
                .data,
            )
            if not rows:
                break
            ids = [row["id"] for row in rows]
            self._client.table(table).delete().in_("id", ids).execute()
            total += len(ids)
            if len(rows) < BATCH_SIZE:
                break
        return total

    def _prune_registry(self) -> int:
        assert REGISTRY_TABLE not in PROTECTED_TABLES
        keep = max(self._registry_keep_versions, 1)  # never below 1, even if config says 0
        pruned = 0
        for config_type in CONFIG_TYPES:
            rows = cast(
                "list[dict[str, Any]]",
                self._client.table(REGISTRY_TABLE)
                .select("id, version")
                .eq("config_type", config_type)
                .order("version", desc=True)
                .execute()
                .data,
            )
            stale = rows[keep:]
            if not stale:
                continue
            ids = [row["id"] for row in stale]
            self._client.table(REGISTRY_TABLE).delete().in_("id", ids).execute()
            pruned += len(ids)
        return pruned
