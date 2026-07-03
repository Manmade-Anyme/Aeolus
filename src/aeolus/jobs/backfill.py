"""OutcomeBackfillJob (TASK-012 ADR) -- after-the-fact enrichment job. Never
runs live/synchronously with the scoring loop; the labels it writes don't
exist yet at signal-time. Invoked once per session after close.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

from supabase import Client, create_client

from aeolus.signals.volatility import implied_expected_move
from aeolus.storage.models import DailyOutlook, HorizonMinutes, OutcomeLabel, SignalSnapshot, StateTransition

from .realized_archetype import classify_realized_archetype

HORIZONS: tuple[HorizonMinutes, ...] = (15, 30, 60)
FORWARD_MATCH_TOLERANCE = timedelta(minutes=5)
FLAT_THRESHOLD_POINTS = 15.0


def _carry(snapshot: SignalSnapshot, category: str, key: str) -> float | None:
    value = snapshot.raw_readings.get(category, {}).get("_carry", {}).get(key)
    return float(value) if value is not None else None


def _nearest_within(
    snapshots: list[SignalSnapshot], target_ts: datetime, tolerance: timedelta
) -> SignalSnapshot | None:
    candidates = [(abs(s.ts - target_ts), s) for s in snapshots]
    if not candidates:
        return None
    delta, nearest = min(candidates, key=lambda pair: pair[0])
    return nearest if delta <= tolerance else None


def _direction(realized_move: float) -> str:
    if realized_move > FLAT_THRESHOLD_POINTS:
        return "UP"
    if realized_move < -FLAT_THRESHOLD_POINTS:
        return "DOWN"
    return "FLAT"


def _label(
    t0_ts: datetime,
    t0_futures_ltp: float | None,
    t0_spot_ltp: float | None,
    t0_india_vix: float | None,
    all_snapshots: list[SignalSnapshot],
    *,
    snapshot_id: Any = None,
    transition_id: Any = None,
) -> list[OutcomeLabel]:
    if t0_futures_ltp is None:
        return []

    labels: list[OutcomeLabel] = []
    for horizon in HORIZONS:
        target = _nearest_within(all_snapshots, t0_ts + timedelta(minutes=horizon), FORWARD_MATCH_TOLERANCE)
        if target is None:
            continue

        target_futures_ltp = _carry(target, "order_flow", "futures_ltp")
        if target_futures_ltp is None:
            continue

        realized_move = target_futures_ltp - t0_futures_ltp

        t0_implied_move = implied_expected_move(t0_spot_ltp, t0_india_vix)
        target_implied_move = implied_expected_move(
            _carry(target, "volatility", "spot_ltp"), _carry(target, "volatility", "india_vix")
        )
        if t0_implied_move is None or target_implied_move is None:
            continue
        straddle_price_change = target_implied_move - t0_implied_move

        labels.append(
            OutcomeLabel(
                snapshot_id=snapshot_id,
                transition_id=transition_id,
                horizon_minutes=horizon,
                straddle_price_change=straddle_price_change,
                realized_move=realized_move,
                direction=_direction(realized_move),  # type: ignore[arg-type]
            )
        )
    return labels


class OutcomeBackfillJob:
    def __init__(self, supabase_url: str, supabase_key: str) -> None:
        self._client: Client = create_client(supabase_url, supabase_key)

    def run(self, session_date: date) -> None:
        snapshots = self._load_snapshots(session_date)
        transitions = self._load_transitions(session_date)

        all_labels: list[OutcomeLabel] = []
        for snapshot in snapshots:
            all_labels.extend(
                _label(
                    snapshot.ts,
                    _carry(snapshot, "order_flow", "futures_ltp"),
                    _carry(snapshot, "volatility", "spot_ltp"),
                    _carry(snapshot, "volatility", "india_vix"),
                    snapshots,
                    snapshot_id=snapshot.id,
                )
            )

        for transition_id, transition_ts in transitions:
            nearest = _nearest_within(snapshots, transition_ts, FORWARD_MATCH_TOLERANCE)
            if nearest is None:
                continue
            all_labels.extend(
                _label(
                    transition_ts,
                    _carry(nearest, "order_flow", "futures_ltp"),
                    _carry(nearest, "volatility", "spot_ltp"),
                    _carry(nearest, "volatility", "india_vix"),
                    snapshots,
                    transition_id=transition_id,
                )
            )

        self._upsert_labels(all_labels)

        archetype = classify_realized_archetype(snapshots)
        if archetype is not None:
            self._client.table(DailyOutlook.TABLE).update({"realized_archetype": archetype}).eq(
                "session_date", session_date.isoformat()
            ).execute()

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

    def _load_transitions(self, session_date: date) -> list[tuple[Any, datetime]]:
        start = datetime.combine(session_date, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        rows = cast(
            "list[dict[str, Any]]",
            self._client.table(StateTransition.TABLE)
            .select("id, ts")
            .gte("ts", start.isoformat())
            .lt("ts", end.isoformat())
            .execute()
            .data,
        )
        return [(row["id"], datetime.fromisoformat(row["ts"])) for row in rows]

    def _upsert_labels(self, labels: list[OutcomeLabel]) -> None:
        snapshot_sourced = [
            label.model_dump(mode="json", exclude={"id"})
            for label in labels
            if label.snapshot_id is not None
        ]
        transition_sourced = [
            label.model_dump(mode="json", exclude={"id"})
            for label in labels
            if label.transition_id is not None
        ]
        if snapshot_sourced:
            self._client.table(OutcomeLabel.TABLE).upsert(
                snapshot_sourced, on_conflict="snapshot_id,horizon_minutes"
            ).execute()
        if transition_sourced:
            self._client.table(OutcomeLabel.TABLE).upsert(
                transition_sourced, on_conflict="transition_id,horizon_minutes"
            ).execute()
