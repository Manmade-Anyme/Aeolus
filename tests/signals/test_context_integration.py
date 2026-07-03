import inspect
from datetime import date

import pytest

from aeolus.signals.context import (
    DayProfileShape,
    build_volume_price_histogram,
    dte,
    futures_basis_drift,
    gap_classification,
    prior_day_profile_shape,
    value_area,
)

BAND = (5.0, 100.0)  # low=5.0 used as the flat-threshold gate where relevant


# --- dte ---


def test_dte_computes_days_between_dates():
    assert dte(date(2026, 7, 6), date(2026, 7, 7)) == 1


def test_dte_none_expiry_returns_none():
    assert dte(date(2026, 7, 6), None) is None


def test_dte_zero_on_expiry_day_itself():
    assert dte(date(2026, 7, 7), date(2026, 7, 7)) == 0


def test_dte_negative_for_a_stale_past_expiry():
    assert dte(date(2026, 7, 8), date(2026, 7, 7)) == -1


# --- build_volume_price_histogram / value_area ---


def test_histogram_buckets_and_accumulates_volume():
    history = [(100.4, 10.0), (100.6, 5.0), (105.0, 20.0)]
    histogram = build_volume_price_histogram(history, bucket_size=5.0)
    assert histogram == {100.0: 15.0, 105.0: 20.0}


def test_value_area_empty_histogram_returns_none():
    assert value_area({}, 0.70) is None


def test_value_area_poc_is_max_volume_bucket():
    histogram = {100.0: 10.0, 105.0: 30.0, 110.0: 50.0, 115.0: 20.0, 120.0: 5.0}
    poc, va_low, va_high = value_area(histogram, 0.70)
    assert poc == 110.0
    assert va_low <= poc <= va_high


def test_value_area_band_holds_at_least_area_pct_of_volume():
    histogram = {100.0: 10.0, 105.0: 30.0, 110.0: 50.0, 115.0: 20.0, 120.0: 5.0}
    total = sum(histogram.values())
    _, va_low, va_high = value_area(histogram, 0.70)
    in_band = sum(v for p, v in histogram.items() if va_low <= p <= va_high)
    assert in_band / total >= 0.70


def test_value_area_single_bucket_returns_degenerate_band():
    assert value_area({100.0: 42.0}, 0.70) == (100.0, 100.0, 100.0)


# --- prior_day_profile_shape ---


def test_profile_shape_missing_input_falls_back():
    shape, result = prior_day_profile_shape(None, None, None, None, [], 1.3, 0.8, BAND)
    assert shape is None
    assert result == (None, BAND, 0.5, "prior_day_profile_shape: no data")


def test_profile_shape_zero_width_range_falls_back():
    shape, result = prior_day_profile_shape(
        24500.0, 24500.0, 24500.0, (24500.0, 24490.0, 24510.0), [50.0], 1.3, 0.8, BAND
    )
    assert shape is None
    assert result[:3] == (None, BAND, 0.5)


def test_profile_shape_trend_day_scores_go_favorable():
    # day_range=200 vs trailing average 100 -> expansion=2.0 (> 1.3 threshold)
    # close_location = (24690-24500)/200 = 0.95 -> beyond extreme_threshold=0.8
    shape, result = prior_day_profile_shape(
        prior_day_high=24700.0,
        prior_day_low=24500.0,
        prior_close=24690.0,
        prior_value_area=(24650.0, 24600.0, 24700.0),
        trailing_average_range_history=[100.0, 100.0, 100.0],
        expansion_threshold=1.3,
        extreme_threshold=0.8,
        reference_band=BAND,
    )
    assert shape is DayProfileShape.TREND
    _, band, sub_score, _ = result
    assert band == BAND
    assert sub_score > 0.5


def test_profile_shape_balanced_day_scores_no_go_favorable():
    # day_range=200 vs trailing average 190 -> expansion ~1.05, below 1.3 threshold
    # close_location = (24600-24500)/200 = 0.5 -> mid-range, not extreme
    shape, result = prior_day_profile_shape(
        prior_day_high=24700.0,
        prior_day_low=24500.0,
        prior_close=24600.0,
        prior_value_area=(24600.0, 24550.0, 24650.0),
        trailing_average_range_history=[190.0, 190.0, 190.0],
        expansion_threshold=1.3,
        extreme_threshold=0.8,
        reference_band=BAND,
    )
    assert shape is DayProfileShape.BALANCED
    _, band, sub_score, _ = result
    assert band == BAND
    assert sub_score < 0.5


# --- gap_classification ---


def test_gap_classification_missing_input_falls_back():
    result = gap_classification(None, 24500.0, None, None, [], BAND)
    assert result == (None, BAND, 0.5, "gap_classification: no data")


def test_gap_classification_no_gap_opens_inside_value_area():
    result = gap_classification(
        session_open=24550.0,
        current_futures_ltp=24560.0,
        prior_value_area=(24550.0, 24500.0, 24600.0),
        prior_close=24540.0,
        trailing_gap_magnitude_history=[],
        reference_band=BAND,
    )
    raw_value, band, sub_score, _ = result
    assert raw_value == 0.0
    assert band == BAND
    assert sub_score < 0.5


def test_gap_classification_gap_and_go_scores_go_favorable():
    # opens above yesterday's VA high (600), keeps moving further up -> continuation
    result = gap_classification(
        session_open=24650.0,
        current_futures_ltp=24700.0,
        prior_value_area=(24550.0, 24500.0, 24600.0),
        prior_close=24540.0,
        trailing_gap_magnitude_history=[20.0],
        reference_band=BAND,
    )
    raw_value, band, sub_score, _ = result
    assert raw_value == pytest.approx(50.0)  # 24650 - 24600
    assert band == BAND
    assert sub_score > 0.5


def test_gap_classification_gap_and_fill_scores_no_go_favorable():
    # opens above yesterday's VA high, but reverts back down toward it -> fill
    result = gap_classification(
        session_open=24650.0,
        current_futures_ltp=24610.0,
        prior_value_area=(24550.0, 24500.0, 24600.0),
        prior_close=24540.0,
        trailing_gap_magnitude_history=[20.0],
        reference_band=BAND,
    )
    raw_value, band, sub_score, _ = result
    assert raw_value == pytest.approx(50.0)
    assert band == BAND
    assert sub_score < 0.5


# --- futures_basis_drift ---


def test_basis_drift_insufficient_history_falls_back():
    result = futures_basis_drift([100.0], [24500.0], [], BAND)
    assert result == (None, BAND, 0.5, "futures_basis_drift: no data")


def test_basis_drift_flat_price_trend_scores_neutral():
    # price_trend = 1, below flat_threshold (BAND low = 5.0)
    raw_value, band, sub_score, _ = futures_basis_drift(
        [40.0, 60.0], [24500.0, 24501.0], [50.0], BAND
    )
    assert raw_value == 20.0
    assert band == BAND
    assert sub_score == 0.5


def test_basis_drift_confirming_scores_go_favorable():
    # price up 20 (beyond flat threshold), basis also widening -> confirming
    _, _, sub_score, _ = futures_basis_drift([40.0, 80.0], [24500.0, 24520.0], [20.0], BAND)
    assert sub_score > 0.5


def test_basis_drift_diverging_scores_no_go_favorable():
    # price up 20, basis narrowing -> diverging
    _, _, sub_score, _ = futures_basis_drift([80.0, 40.0], [24500.0, 24520.0], [20.0], BAND)
    assert sub_score < 0.5


# --- constraint check: no clock-time branching ---


def test_no_function_takes_a_clock_argument():
    for fn in (
        build_volume_price_histogram,
        value_area,
        prior_day_profile_shape,
        gap_classification,
        futures_basis_drift,
    ):
        params = inspect.signature(fn).parameters
        for name in params:
            assert "time" not in name.lower() and "clock" not in name.lower(), (
                f"{fn.__name__} takes {name!r} — signals must never branch on wall-clock time"
            )
