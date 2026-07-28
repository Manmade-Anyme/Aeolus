"""Pydantic models mirroring the four TASK-001 tables (docs/DATA_MODEL.md).

Later tasks read/write these tables through these models rather than raw dicts.
Signal modules (TASK-003..007) never touch this layer directly — they return
the standard (raw_value, reference_band, sub_score, reason_string) tuple to
the composite scorer (TASK-008), which is the sole writer of
SignalSnapshot/StateTransition rows.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

MarketState = Literal["NO_GO", "PREPARE", "GO"]
SystemStatus = Literal["OK", "STALE", "DISCONNECTED"]
ConfigType = Literal["EXPIRY", "NON_EXPIRY"]
DayArchetype = Literal[
    "clean_trend",
    "grinding_trend",
    "pinned_range",
    "choppy_range",
    "breakout_transition",
    "event_gap",
    "double_distribution",
]
OutcomeDirection = Literal["UP", "DOWN", "FLAT"]
HorizonMinutes = Literal[15, 30, 60]


class SignalSnapshot(BaseModel):
    """One row per computation cycle. Written by TASK-008 (composite scorer)."""

    TABLE: ClassVar[str] = "signal_snapshots"

    # Read-only view (migration 0013) returning the chronologically final row
    # per session_date. Cross-session trailing histories MUST be seeded from
    # this, not from TABLE: the engine writes one row per 5s cycle (~4,500
    # rows/session), so any bounded `.limit()` over TABLE returns rows from a
    # single date and collapses every trailing history to one element.
    EOD_VIEW: ClassVar[str] = "daily_eod_signal_snapshots"

    id: UUID = Field(default_factory=uuid4)
    ts: datetime
    session_date: date
    config_type: ConfigType
    dte: int
    raw_readings: dict[str, Any]
    sub_scores: dict[str, float]
    composite_score: float
    market_state: MarketState
    system_status: SystemStatus
    reasons: dict[str, str]


class StateTransition(BaseModel):
    """Written only on confirmed, debounced state flips. Written by TASK-008."""

    TABLE: ClassVar[str] = "state_transitions"

    id: UUID = Field(default_factory=uuid4)
    ts: datetime
    from_state: MarketState
    to_state: MarketState
    trigger_categories: list[str]
    reason: str


class DailyOutlook(BaseModel):
    """One row per trading day. Written pre-market by TASK-009, backfilled by TASK-012."""

    TABLE: ClassVar[str] = "daily_outlook"

    id: UUID = Field(default_factory=uuid4)
    session_date: date
    predicted_archetype: DayArchetype
    archetype_confidence: float
    contributing_inputs: dict[str, Any]
    trend_exhaustion_flag: bool
    straddle_level_vs_history: float
    realized_archetype: DayArchetype | None = None


class OutcomeLabel(BaseModel):
    """Backfilled enrichment, never written live. Written by TASK-012 only.

    The "at least one of snapshot_id/transition_id" rule is enforced only here
    (_has_source below), not at the DB level — a DB CHECK constraint doing this
    conflicted with ON DELETE SET NULL and was dropped (migration 0006)."""

    TABLE: ClassVar[str] = "outcome_labels"

    id: UUID = Field(default_factory=uuid4)
    snapshot_id: UUID | None = None
    transition_id: UUID | None = None
    horizon_minutes: HorizonMinutes
    straddle_price_change: float
    realized_move: float
    direction: OutcomeDirection

    @model_validator(mode="after")
    def _has_source(self) -> OutcomeLabel:
        if self.snapshot_id is None and self.transition_id is None:
            raise ValueError("OutcomeLabel requires snapshot_id or transition_id")
        return self
