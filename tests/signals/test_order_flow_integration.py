import inspect
from datetime import datetime, timezone

import pytest

from aeolus.ingestion.models import IngestionSnapshot
from aeolus.signals.order_flow import (
    _near_extreme,
    _tick_classify,
    cvd_direction_and_divergence,
    delta_imbalance_and_absorption,
    volume_participation_range,
)

BAND = (5.0, 100.0)  # low=5.0 used as the flat-threshold gate where relevant


def _snapshot(
    futures_ltp: float | None = 24500.0,
    volume: int | None = None,
    total_buy_quantity: int | None = None,
    total_sell_quantity: int | None = None,
    day_high: float | None = None,
    day_low: float | None = None,
) -> IngestionSnapshot:
    return IngestionSnapshot(
        ts=datetime.now(timezone.utc),
        spot_ltp=futures_ltp,
        futures_ltp=futures_ltp,
        futures_basis=None,
        depth=None,
        option_chain=[],
        india_vix=None,
        gift_nifty=None,
        volume=volume,
        total_buy_quantity=total_buy_quantity,
        total_sell_quantity=total_sell_quantity,
        day_high=day_high,
        day_low=day_low,
        expiry_date=None,
        system_status="OK",
        system_status_detail={"ws": "OK", "option_chain": "OK"},
    )


# --- cvd_direction_and_divergence ---


def test_cvd_empty_history_falls_back():
    result = cvd_direction_and_divergence([], [], [], BAND)
    assert result == (None, BAND, 0.5, "cvd_direction_and_divergence: no data")


def test_cvd_single_price_point_falls_back():
    result = cvd_direction_and_divergence([100.0], [24500.0], [], BAND)
    assert result[:3] == (None, BAND, 0.5)


def test_cvd_flat_price_trend_scores_neutral():
    # price_trend = 24501 - 24500 = 1, below flat_threshold (BAND low = 5.0)
    raw_value, band, sub_score, _ = cvd_direction_and_divergence(
        [100.0, 200.0], [24500.0, 24501.0], [50.0], BAND
    )
    assert raw_value == 300.0
    assert band == BAND
    assert sub_score == 0.5


def test_cvd_confirming_scores_go_favorable():
    # price up 20 points (beyond flat threshold), CVD also positive -> confirming
    _, _, sub_score, _ = cvd_direction_and_divergence(
        [1000.0, 2000.0], [24500.0, 24520.0], [500.0], BAND
    )
    assert sub_score > 0.5


def test_cvd_diverging_scores_no_go_favorable():
    # price up 20 points, CVD negative -> diverging (fragile move)
    _, _, sub_score, _ = cvd_direction_and_divergence(
        [-1000.0, -2000.0], [24500.0, 24520.0], [500.0], BAND
    )
    assert sub_score < 0.5


def test_tick_classify_price_up_attributes_positive_volume_delta():
    previous = _snapshot(futures_ltp=24500.0, volume=100_000)
    current = _snapshot(futures_ltp=24510.0, volume=100_500)
    assert _tick_classify(current, previous) == 500.0


def test_tick_classify_price_down_attributes_negative_volume_delta():
    previous = _snapshot(futures_ltp=24500.0, volume=100_000)
    current = _snapshot(futures_ltp=24490.0, volume=100_500)
    assert _tick_classify(current, previous) == -500.0


def test_tick_classify_flat_price_attributes_zero_not_split():
    previous = _snapshot(futures_ltp=24500.0, volume=100_000)
    current = _snapshot(futures_ltp=24500.0, volume=100_500)
    assert _tick_classify(current, previous) == 0.0


def test_tick_classify_missing_data_returns_none():
    previous = _snapshot(futures_ltp=None, volume=100_000)
    current = _snapshot(futures_ltp=24510.0, volume=100_500)
    assert _tick_classify(current, previous) is None
    assert _tick_classify(current, None) is None


# --- delta_imbalance_and_absorption ---


def test_absorption_missing_input_falls_back():
    current = _snapshot(total_buy_quantity=None)
    result = delta_imbalance_and_absorption(current, [], 0.1, BAND)
    assert result == (None, BAND, 0.5, "delta_imbalance_and_absorption: no data")


def test_absorption_zero_book_falls_back():
    current = _snapshot(
        total_buy_quantity=0, total_sell_quantity=0, day_high=24600.0, day_low=24400.0
    )
    result = delta_imbalance_and_absorption(current, [], 0.1, BAND)
    assert result[:3] == (None, BAND, 0.5)


def test_absorption_zero_width_range_falls_back():
    current = _snapshot(
        total_buy_quantity=100, total_sell_quantity=50, day_high=24500.0, day_low=24500.0
    )
    result = delta_imbalance_and_absorption(current, [], 0.1, BAND)
    assert result[:3] == (None, BAND, 0.5)


def test_absorption_confirms_extreme_at_low_scores_go_favorable():
    # near day_low, sell-heavy imbalance (imbalance < 0) -> confirms extreme (break likely)
    current = _snapshot(
        futures_ltp=24405.0,
        total_buy_quantity=300,
        total_sell_quantity=700,
        day_high=24600.0,
        day_low=24400.0,
    )
    _, _, sub_score, _ = delta_imbalance_and_absorption(current, [0.2], 0.1, BAND)
    assert sub_score > 0.5


def test_absorption_opposes_extreme_at_low_scores_no_go_favorable():
    # near day_low, buy-heavy imbalance (imbalance > 0) -> absorption, opposes the break
    current = _snapshot(
        futures_ltp=24405.0,
        total_buy_quantity=700,
        total_sell_quantity=300,
        day_high=24600.0,
        day_low=24400.0,
    )
    _, _, sub_score, _ = delta_imbalance_and_absorption(current, [0.2], 0.1, BAND)
    assert sub_score < 0.5


def test_absorption_confirms_extreme_at_high_scores_go_favorable():
    # near day_high, buy-heavy imbalance (imbalance > 0) -> confirms extreme
    current = _snapshot(
        futures_ltp=24595.0,
        total_buy_quantity=700,
        total_sell_quantity=300,
        day_high=24600.0,
        day_low=24400.0,
    )
    _, _, sub_score, _ = delta_imbalance_and_absorption(current, [0.2], 0.1, BAND)
    assert sub_score > 0.5


def test_absorption_mid_range_weaker_baseline_lean():
    current = _snapshot(
        futures_ltp=24500.0,
        total_buy_quantity=700,
        total_sell_quantity=300,
        day_high=24600.0,
        day_low=24400.0,
    )
    raw_value, band, sub_score, _ = delta_imbalance_and_absorption(current, [0.2], 0.1, BAND)
    assert raw_value == pytest.approx(0.4)
    assert band == BAND
    # magnitude_pct = 1.0 (0.2 <= 0.4), capped baseline lean: 0.5 + 0.25*1.0
    assert sub_score == pytest.approx(0.75)


# --- volume_participation_range ---


def test_volume_range_missing_input_falls_back():
    current = _snapshot(volume=None)
    result = volume_participation_range(current, None, 1_000_000.0, 0.1, 50.0, [], BAND)
    assert result == (None, BAND, 0.5, "volume_participation_range: no data")


def test_volume_range_not_yet_established_below_threshold():
    current = _snapshot(volume=50_000, day_high=24510.0, day_low=24490.0)
    result = volume_participation_range(current, None, 1_000_000.0, 0.5, 10.0, [], BAND)
    assert result[:3] == (None, BAND, 0.5)


def test_volume_range_degenerate_range_not_established():
    # volume threshold crossed but range too narrow (< min_range_width)
    current = _snapshot(volume=600_000, day_high=24500.5, day_low=24499.5)
    result = volume_participation_range(current, None, 1_000_000.0, 0.5, 50.0, [], BAND)
    assert result[:3] == (None, BAND, 0.5)


def test_volume_range_establishes_and_scores_inside():
    current = _snapshot(
        futures_ltp=24500.0, volume=600_000, day_high=24600.0, day_low=24400.0
    )
    raw_value, band, sub_score, reason = volume_participation_range(
        current, None, 1_000_000.0, 0.5, 50.0, [], BAND
    )
    assert raw_value == 0.0
    assert band == BAND
    assert sub_score <= 0.5  # inside established range -> compression, NO-GO-favorable
    assert "established_low" in reason and "established_high" in reason


def test_volume_range_breakout_scores_go_favorable():
    established = (24400.0, 24600.0)
    current = _snapshot(futures_ltp=24650.0, volume=650_000, day_high=24650.0, day_low=24400.0)
    raw_value, band, sub_score, _ = volume_participation_range(
        current, established, 1_000_000.0, 0.5, 50.0, [20.0], BAND
    )
    assert raw_value == pytest.approx(50.0)  # 24650 - 24600
    assert band == BAND
    assert sub_score > 0.5


def test_volume_range_holds_established_range_across_calls_via_context():
    established = (24400.0, 24600.0)
    current = _snapshot(futures_ltp=24500.0, volume=700_000, day_high=24650.0, day_low=24400.0)
    _, _, _, reason = volume_participation_range(
        current, established, 1_000_000.0, 0.5, 50.0, [], BAND
    )
    assert "established_low=24400.00" in reason
    assert "established_high=24600.00" in reason


# --- private helpers ---


def test_near_extreme_zero_width_range_returns_false_false():
    assert _near_extreme(24500.0, 24500.0, 24500.0, 0.1) == (False, False)


def test_near_extreme_detects_both_sides():
    assert _near_extreme(24410.0, 24400.0, 24600.0, 0.1) == (True, False)
    assert _near_extreme(24590.0, 24400.0, 24600.0, 0.1) == (False, True)


# --- constraint check: no clock-time branching ---


def test_no_function_takes_a_clock_or_datetime_argument():
    for fn in (
        cvd_direction_and_divergence,
        delta_imbalance_and_absorption,
        volume_participation_range,
    ):
        params = inspect.signature(fn).parameters
        for name in params:
            assert "time" not in name.lower() and "clock" not in name.lower(), (
                f"{fn.__name__} takes {name!r} — signals must never branch on wall-clock time"
            )
