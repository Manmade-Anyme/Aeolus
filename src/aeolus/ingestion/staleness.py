"""Heartbeat-based staleness tracking -> system_status (TASK-002 ADR).

Elapsed-time-since-last-touch only, never wall-clock branching -- this module
answers "how long since we last heard from this path", never "what time is
it now". This module is the sole owner of staleness detection; downstream
signal modules (TASK-003..007) need none of their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from aeolus.ingestion.models import PathStatus, SystemStatus

DEFAULT_STALE_AFTER = timedelta(seconds=15)
DEFAULT_DISCONNECTED_AFTER = timedelta(seconds=60)


@dataclass
class PathHeartbeat:
    last_seen: datetime | None = None
    stale_after: timedelta = field(default=DEFAULT_STALE_AFTER)
    disconnected_after: timedelta = field(default=DEFAULT_DISCONNECTED_AFTER)

    def touch(self, ts: datetime) -> None:
        self.last_seen = ts

    def status(self, now: datetime) -> PathStatus:
        if self.last_seen is None:
            return "DISCONNECTED"
        elapsed = now - self.last_seen
        if elapsed >= self.disconnected_after:
            return "DISCONNECTED"
        if elapsed >= self.stale_after:
            return "STALE"
        return "OK"


def aggregate_status(detail: dict[str, PathStatus]) -> SystemStatus:
    """Worst-of across tracked paths. UNAVAILABLE paths (e.g. a feed that
    structurally doesn't exist, like GIFT Nifty) are excluded by callers
    before this is invoked -- they're never in `detail` to begin with."""
    values = set(detail.values())
    if "DISCONNECTED" in values:
        return "DISCONNECTED"
    if "STALE" in values:
        return "STALE"
    return "OK"


class StalenessTracker:
    """Tracks per-path heartbeats (ws, option_chain) and aggregates status."""

    def __init__(self) -> None:
        self._paths: dict[str, PathHeartbeat] = {
            "ws": PathHeartbeat(),
            "option_chain": PathHeartbeat(),
        }

    def touch(self, path: str, ts: datetime) -> None:
        self._paths[path].touch(ts)

    def detail(self, now: datetime) -> dict[str, PathStatus]:
        return {name: hb.status(now) for name, hb in self._paths.items()}

    def aggregate(self, now: datetime) -> SystemStatus:
        return aggregate_status(self.detail(now))
