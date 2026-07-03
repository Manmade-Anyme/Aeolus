from datetime import date, datetime, timedelta, timezone

from aeolus.jobs.realized_archetype import classify_realized_archetype
from aeolus.storage.models import SignalSnapshot

_BASE_TS = datetime(2026, 7, 3, 4, 0, tzinfo=timezone.utc)


def _snapshot(minute: int, futures_ltp: float, current_iv: float, gap_raw_value: float = 0.0) -> SignalSnapshot:
    return SignalSnapshot(
        ts=_BASE_TS + timedelta(minutes=minute),
        session_date=date(2026, 7, 3),
        config_type="NON_EXPIRY",
        dte=3,
        raw_readings={
            "order_flow": {"_carry": {"futures_ltp": futures_ltp}},
            "volatility": {"_carry": {"current_iv": current_iv, "spot_ltp": futures_ltp}},
            "context": {"gap_classification": {"raw_value": gap_raw_value}},
        },
        sub_scores={},
        composite_score=0.5,
        market_state="PREPARE",
        system_status="OK",
        reasons={},
    )


def test_insufficient_data_returns_none():
    assert classify_realized_archetype([]) is None
    assert classify_realized_archetype([_snapshot(0, 100.0, 14.0)]) is None


def test_clean_trend_rising_price_expanding_iv():
    snapshots = [
        _snapshot(0, 100.0, 12.0),
        _snapshot(60, 110.0, 13.0),
        _snapshot(120, 125.0, 15.0),
    ]
    assert classify_realized_archetype(snapshots) == "clean_trend"


def test_grinding_trend_rising_price_contracting_iv():
    snapshots = [
        _snapshot(0, 100.0, 16.0),
        _snapshot(60, 110.0, 14.0),
        _snapshot(120, 125.0, 12.0),
    ]
    assert classify_realized_archetype(snapshots) == "grinding_trend"


def test_pinned_range_mid_range_close_contracting_iv():
    # close sits near the middle of the session's own range -> balanced
    snapshots = [
        _snapshot(0, 100.0, 16.0),
        _snapshot(60, 105.0, 14.0),
        _snapshot(120, 102.0, 12.0),
    ]
    assert classify_realized_archetype(snapshots) == "pinned_range"


def test_choppy_range_mid_range_close_expanding_iv():
    snapshots = [
        _snapshot(0, 100.0, 12.0),
        _snapshot(60, 108.0, 15.0),
        _snapshot(120, 103.0, 18.0),
    ]
    assert classify_realized_archetype(snapshots) == "choppy_range"


def test_breakout_transition_range_then_trend():
    # first half: balanced (closes mid-range), second half: trend (closes at an extreme)
    snapshots = [
        _snapshot(0, 100.0, 14.0),
        _snapshot(30, 105.0, 14.0),
        _snapshot(60, 102.5, 14.0),  # first half closes near its own midpoint -> balanced
        _snapshot(90, 102.5, 14.0),
        _snapshot(120, 115.0, 14.0),
        _snapshot(150, 130.0, 14.0),  # second half closes at its own extreme -> trend
    ]
    assert classify_realized_archetype(snapshots) == "breakout_transition"


def test_event_gap_open_beyond_value_area_with_iv_spike_then_crush():
    snapshots = [
        _snapshot(0, 100.0, 14.0, gap_raw_value=25.0),  # opened beyond prior value area
        _snapshot(60, 100.5, 22.0),  # IV spikes mid-session
        _snapshot(120, 100.2, 13.0),  # IV crushes back down, price roughly flat/balanced
    ]
    assert classify_realized_archetype(snapshots) == "event_gap"


def test_double_distribution_falls_through_to_base_quadrant():
    # No override triggers for a plain trend+expanding session -- double_distribution
    # is never returned, per the ADR's explicit known-limitation.
    snapshots = [
        _snapshot(0, 100.0, 12.0),
        _snapshot(60, 110.0, 13.0),
        _snapshot(120, 125.0, 15.0),
    ]
    result = classify_realized_archetype(snapshots)
    assert result != "double_distribution"
    assert result == "clean_trend"
