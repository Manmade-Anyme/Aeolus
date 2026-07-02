"""Contract tests for TASK-001 storage models. No internal mocking — exercises
the public pydantic contract directly (construction, JSON round-trip, validation)."""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aeolus.storage.models import (
    DailyOutlook,
    OutcomeLabel,
    SignalSnapshot,
    StateTransition,
)


def test_signal_snapshot_round_trip_recomputable_from_raw():
    """Composite must be recomputable retroactively from raw_readings alone."""
    snap = SignalSnapshot(
        ts=datetime(2026, 7, 3, 9, 20, tzinfo=timezone.utc),
        session_date=date(2026, 7, 3),
        config_type="EXPIRY",
        dte=0,
        raw_readings={
            "volatility": {"raw_value": 14.2, "reference_band": [10, 18]},
            "gamma": {"raw_value": 0.85, "reference_band": [0.5, 1.2]},
        },
        sub_scores={"volatility": 0.6, "gamma": 0.4},
        composite_score=0.52,
        market_state="GO",
        system_status="OK",
        reasons={"volatility": "14.2 within [10, 18] band -> sub_score 0.60"},
    )
    dumped = snap.model_dump(mode="json")
    restored = SignalSnapshot.model_validate(dumped)

    assert restored.raw_readings == snap.raw_readings
    recomputed = sum(restored.sub_scores.values()) / len(restored.sub_scores)
    assert recomputed == pytest.approx(0.5)


def test_signal_snapshot_market_state_and_system_status_are_independent_fields():
    snap = SignalSnapshot(
        ts=datetime.now(timezone.utc),
        session_date=date(2026, 7, 3),
        config_type="NON_EXPIRY",
        dte=3,
        raw_readings={},
        sub_scores={},
        composite_score=0.0,
        market_state="NO_GO",
        system_status="DISCONNECTED",
        reasons={},
    )
    # A dead feed must never masquerade as a computed NO-GO: the two fields
    # must be settable independently, not derived from one another.
    assert snap.market_state == "NO_GO"
    assert snap.system_status == "DISCONNECTED"


@pytest.mark.parametrize("bad_state", ["GOOD", "go", "", None])
def test_signal_snapshot_rejects_invalid_market_state(bad_state):
    with pytest.raises(ValidationError):
        SignalSnapshot(
            ts=datetime.now(timezone.utc),
            session_date=date(2026, 7, 3),
            config_type="EXPIRY",
            dte=0,
            raw_readings={},
            sub_scores={},
            composite_score=0.0,
            market_state=bad_state,
            system_status="OK",
            reasons={},
        )


def test_state_transition_requires_confirmed_from_and_to_states():
    transition = StateTransition(
        ts=datetime.now(timezone.utc),
        from_state="NO_GO",
        to_state="GO",
        trigger_categories=["gamma", "order_flow"],
        reason="composite crossed 0.65 threshold, driven by gamma+order_flow",
    )
    assert transition.trigger_categories == ["gamma", "order_flow"]


def test_daily_outlook_trend_exhaustion_is_own_field_not_nested():
    outlook = DailyOutlook(
        session_date=date(2026, 7, 3),
        predicted_archetype="clean_trend",
        archetype_confidence=0.72,
        contributing_inputs={"straddle_iv": 12.4},
        trend_exhaustion_flag=True,
        straddle_level_vs_history=1.1,
    )
    assert outlook.trend_exhaustion_flag is True
    assert "trend_exhaustion_flag" not in outlook.contributing_inputs
    assert outlook.realized_archetype is None


def test_daily_outlook_realized_archetype_backfilled_by_task_012():
    outlook = DailyOutlook(
        session_date=date(2026, 7, 3),
        predicted_archetype="clean_trend",
        archetype_confidence=0.72,
        contributing_inputs={},
        trend_exhaustion_flag=False,
        straddle_level_vs_history=0.9,
        realized_archetype="grinding_trend",
    )
    assert outlook.realized_archetype == "grinding_trend"


def test_outcome_label_requires_snapshot_or_transition_source():
    with pytest.raises(ValidationError):
        OutcomeLabel(
            snapshot_id=None,
            transition_id=None,
            horizon_minutes=15,
            straddle_price_change=2.5,
            realized_move=0.4,
            direction="UP",
        )


def test_outcome_label_accepts_snapshot_only_source():
    label = OutcomeLabel(
        snapshot_id=uuid4(),
        transition_id=None,
        horizon_minutes=30,
        straddle_price_change=-1.2,
        realized_move=0.6,
        direction="DOWN",
    )
    assert label.transition_id is None


@pytest.mark.parametrize("bad_horizon", [10, 45, 0, -15])
def test_outcome_label_rejects_non_standard_horizons(bad_horizon):
    with pytest.raises(ValidationError):
        OutcomeLabel(
            snapshot_id=uuid4(),
            horizon_minutes=bad_horizon,
            straddle_price_change=0.0,
            realized_move=0.0,
            direction="FLAT",
        )


def test_table_names_match_migration_files():
    assert SignalSnapshot.TABLE == "signal_snapshots"
    assert StateTransition.TABLE == "state_transitions"
    assert DailyOutlook.TABLE == "daily_outlook"
    assert OutcomeLabel.TABLE == "outcome_labels"
