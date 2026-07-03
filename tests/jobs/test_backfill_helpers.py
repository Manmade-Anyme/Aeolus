from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from aeolus.jobs.backfill import FLAT_THRESHOLD_POINTS, _direction, _label, _nearest_within
from aeolus.storage.models import SignalSnapshot

_BASE_TS = datetime(2026, 7, 3, 4, 0, tzinfo=timezone.utc)


def _snapshot(minute: int, futures_ltp: float | None, spot_ltp: float | None, india_vix: float | None) -> SignalSnapshot:
    carry: dict = {}
    if futures_ltp is not None:
        carry["futures_ltp"] = futures_ltp
    return SignalSnapshot(
        ts=_BASE_TS + timedelta(minutes=minute),
        session_date=date(2026, 7, 3),
        config_type="NON_EXPIRY",
        dte=3,
        raw_readings={
            "order_flow": {"_carry": carry},
            "volatility": {
                "_carry": {
                    k: v
                    for k, v in {"spot_ltp": spot_ltp, "india_vix": india_vix}.items()
                    if v is not None
                }
            },
        },
        sub_scores={},
        composite_score=0.5,
        market_state="PREPARE",
        system_status="OK",
        reasons={},
    )


def test_direction_thresholds():
    assert _direction(FLAT_THRESHOLD_POINTS + 1) == "UP"
    assert _direction(-FLAT_THRESHOLD_POINTS - 1) == "DOWN"
    assert _direction(5.0) == "FLAT"
    assert _direction(-5.0) == "FLAT"


def test_nearest_within_tolerance():
    snapshots = [_snapshot(0, 100.0, 100.0, 14.0), _snapshot(20, 101.0, 101.0, 14.0)]
    nearest = _nearest_within(snapshots, _BASE_TS + timedelta(minutes=16), timedelta(minutes=5))
    assert nearest is snapshots[1]


def test_nearest_within_tolerance_no_match():
    snapshots = [_snapshot(0, 100.0, 100.0, 14.0)]
    nearest = _nearest_within(snapshots, _BASE_TS + timedelta(minutes=30), timedelta(minutes=5))
    assert nearest is None


def test_label_produces_all_horizons_when_matches_exist():
    all_snapshots = [
        _snapshot(0, 100.0, 100.0, 14.0),
        _snapshot(15, 120.0, 100.0, 14.0),
        _snapshot(30, 130.0, 100.0, 16.0),
        _snapshot(60, 140.0, 100.0, 18.0),
    ]
    labels = _label(
        _BASE_TS, 100.0, 100.0, 14.0, all_snapshots, snapshot_id=uuid4()
    )
    horizons = {label.horizon_minutes for label in labels}
    assert horizons == {15, 30, 60}
    label_15 = next(label for label in labels if label.horizon_minutes == 15)
    assert label_15.realized_move == 20.0
    assert label_15.direction == "UP"


def test_label_skips_horizon_with_no_forward_match():
    all_snapshots = [_snapshot(0, 100.0, 100.0, 14.0)]  # nothing at +15/+30/+60
    labels = _label(_BASE_TS, 100.0, 100.0, 14.0, all_snapshots, snapshot_id=uuid4())
    assert labels == []


def test_label_returns_empty_when_t0_futures_ltp_missing():
    labels = _label(_BASE_TS, None, 100.0, 14.0, [], snapshot_id=uuid4())
    assert labels == []
