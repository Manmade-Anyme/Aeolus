import inspect

import pytest

from aeolus.ingestion.models import Greeks, OptionStrike
from aeolus.signals.gamma import (
    _flip_level,
    _net_gamma_by_strike,
    gex_regime,
    spot_distance_from_flip,
)

BAND = (0.0, 1_000_000.0)
DIST_BAND = (0.0, 0.5)
LOT_SIZE = 65


def _greeks(gamma: float) -> Greeks:
    return Greeks(delta=0.5, gamma=gamma, theta=-2.0, vega=1.2)


def _strike(
    strike: float, call_oi: int, call_gamma: float, put_oi: int, put_gamma: float
) -> OptionStrike:
    return OptionStrike(
        strike=strike,
        call_oi=call_oi,
        put_oi=put_oi,
        call_iv=14.0,
        put_iv=14.0,
        call_greeks=_greeks(call_gamma),
        put_greeks=_greeks(put_gamma),
    )


# --- gex_regime ---


def test_gex_regime_missing_spot_falls_back():
    chain = [_strike(24500, 1000, 0.02, 100, 0.005)]
    result = gex_regime(chain, None, LOT_SIZE, [], BAND)
    assert result == (None, BAND, 0.5, "gex_regime: no data")


def test_gex_regime_thin_oi_falls_back():
    chain = [_strike(24500, 100, 0.02, 100, 0.005)]
    result = gex_regime(chain, 24500.0, LOT_SIZE, [], BAND, min_total_oi=1000)
    assert result[:3] == (None, BAND, 0.5)


def test_gex_regime_sign_convention_call_dominant_vs_put_dominant():
    call_dominant = [_strike(24500, 1000, 0.02, 100, 0.005)]
    put_dominant = [_strike(24500, 100, 0.005, 1000, 0.02)]

    raw_call, _, score_call, _ = gex_regime(call_dominant, 24500.0, LOT_SIZE, [10.0], BAND)
    raw_put, _, score_put, _ = gex_regime(put_dominant, 24500.0, LOT_SIZE, [10.0], BAND)

    assert raw_call > 0  # dealer net long gamma -> dampening
    assert raw_put < 0  # dealer net short gamma -> amplifying
    assert score_put > 0.5 > score_call


def test_gex_regime_magnitude_scales_sub_score():
    # put-dominant (negative/amplifying) chain, fixed
    chain = [_strike(24500, 100, 0.005, 1000, 0.02)]

    raw_value, _, _, _ = gex_regime(chain, 24500.0, LOT_SIZE, [], BAND)
    weak_history = [abs(raw_value) * 10]  # our reading sits at a low percentile
    strong_history = [abs(raw_value) / 10]  # our reading sits at a high percentile

    _, _, weak_score, _ = gex_regime(chain, 24500.0, LOT_SIZE, weak_history, BAND)
    _, _, strong_score, _ = gex_regime(chain, 24500.0, LOT_SIZE, strong_history, BAND)

    assert weak_score >= 0.5
    assert strong_score > weak_score  # strong negative gamma scores higher than weak


# --- spot_distance_from_flip ---


def test_spot_distance_from_flip_missing_spot_falls_back():
    chain = [_strike(24500, 1000, 0.02, 100, 0.005)]
    result = spot_distance_from_flip(chain, None, DIST_BAND)
    assert result == (None, DIST_BAND, 0.5, "spot_distance_from_flip: no data")


def test_spot_distance_from_flip_thin_oi_falls_back():
    chain = [_strike(24500, 100, 0.02, 100, 0.005)]
    result = spot_distance_from_flip(chain, 24500.0, DIST_BAND, min_total_oi=1000)
    assert result[:3] == (None, DIST_BAND, 0.5)


def test_spot_distance_from_flip_monotonic_chain_returns_none_not_extrapolated():
    # net_gamma same sign at every strike -> cumulative sum never crosses zero
    chain = [
        _strike(24400, 500, 0.02, 0, 0.0),  # net_gamma = +10
        _strike(24500, 500, 0.02, 0, 0.0),  # net_gamma = +10
        _strike(24600, 500, 0.02, 0, 0.0),  # net_gamma = +10
    ]
    raw_value, _, sub_score, _ = spot_distance_from_flip(chain, 24500.0, DIST_BAND)
    assert raw_value is None
    assert sub_score == 0.5

    # gex_regime is unaffected by the absent flip level
    gex_raw, _, _, _ = gex_regime(chain, 24500.0, LOT_SIZE, [], BAND)
    assert gex_raw is not None


def test_spot_distance_from_flip_interpolates_between_bracketing_strikes():
    chain = [
        _strike(24400, 0, 0.0, 1000, 0.01),  # net_gamma = -10
        _strike(24500, 3000, 0.01, 0, 0.0),  # net_gamma = +30
    ]
    expected_flip = 24400 + 100 * (10 / 30)  # 24433.33...

    raw_value, band, sub_score, reason = spot_distance_from_flip(chain, 24450.0, DIST_BAND)

    expected_raw = (24450.0 - expected_flip) / 24450.0 * 100
    assert raw_value == pytest.approx(expected_raw)
    assert band == DIST_BAND
    assert "spot_distance_from_flip" in reason


def test_spot_distance_from_flip_polarity_further_from_flip_scores_higher():
    chain = [
        _strike(24400, 0, 0.0, 1000, 0.01),  # net_gamma = -10
        _strike(24500, 3000, 0.01, 0, 0.0),  # net_gamma = +30
    ]
    _, _, near_score, _ = spot_distance_from_flip(chain, 24434.0, DIST_BAND)
    _, _, far_score, _ = spot_distance_from_flip(chain, 25000.0, DIST_BAND)
    assert far_score > near_score


# --- private helpers ---


def test_net_gamma_by_strike_sorted_ascending():
    chain = [
        _strike(24600, 100, 0.01, 0, 0.0),
        _strike(24400, 100, 0.01, 0, 0.0),
    ]
    pairs = _net_gamma_by_strike(chain)
    assert [strike for strike, _ in pairs] == [24400, 24600]


def test_flip_level_none_for_single_strike():
    assert _flip_level([(24500.0, 10.0)]) is None


# --- constraint check: no clock-time branching ---


def test_no_function_takes_a_clock_or_datetime_argument():
    for fn in (gex_regime, spot_distance_from_flip):
        params = inspect.signature(fn).parameters
        for name in params:
            assert "time" not in name.lower() and "clock" not in name.lower(), (
                f"{fn.__name__} takes {name!r} — signals must never branch on wall-clock time"
            )
