import inspect
from datetime import datetime, timezone

import pytest

from aeolus.ingestion.models import Greeks, IngestionSnapshot, OptionStrike
from aeolus.signals.contract import MIN_PERCENTILE_HISTORY
from aeolus.signals.oi_structure import (
    _classify_buildup,
    _max_pain,
    _pcr,
    _walls,
    max_pain_drift,
    oi_buildup_classification,
    oi_wall_proximity_and_strength,
    pcr_level_and_roc,
)

BAND = (0.0, 1.0)


def _greeks() -> Greeks:
    return Greeks(delta=0.5, gamma=0.01, theta=-2.0, vega=1.2)


def _strike(strike: float, call_oi: int, put_oi: int) -> OptionStrike:
    return OptionStrike(
        strike=strike,
        call_oi=call_oi,
        put_oi=put_oi,
        call_iv=14.0,
        put_iv=14.0,
        call_greeks=_greeks(),
        put_greeks=_greeks(),
    )


def _snapshot(
    spot_ltp: float | None,
    futures_ltp: float | None,
    option_chain: list[OptionStrike],
) -> IngestionSnapshot:
    return IngestionSnapshot(
        ts=datetime.now(timezone.utc),
        spot_ltp=spot_ltp,
        futures_ltp=futures_ltp,
        futures_basis=None,
        depth=None,
        option_chain=option_chain,
        india_vix=None,
        gift_nifty=None,
        volume=None,
        total_buy_quantity=None,
        total_sell_quantity=None,
        day_high=None,
        day_low=None,
        expiry_date=None,
        system_status="OK",
        system_status_detail={"ws": "OK", "option_chain": "OK"},
    )


# --- pcr_level_and_roc ---


def test_pcr_first_cycle_no_previous_falls_back():
    current = _snapshot(24500.0, 24550.0, [_strike(24500, 1000, 1200)])
    result = pcr_level_and_roc(current, None, [], BAND)
    assert result == (None, BAND, 0.5, "pcr_level_and_roc: no data")


def test_pcr_zero_call_oi_falls_back():
    current = _snapshot(24500.0, 24550.0, [_strike(24500, 0, 1200)])
    previous = _snapshot(24500.0, 24550.0, [_strike(24500, 1000, 900)])
    result = pcr_level_and_roc(current, previous, [], BAND)
    assert result[:3] == (None, BAND, 0.5)


def test_pcr_roc_realistic_computation():
    current = _snapshot(24500.0, 24550.0, [_strike(24500, 1000, 1200)])  # pcr = 1.2
    previous = _snapshot(24450.0, 24500.0, [_strike(24500, 1000, 900)])  # pcr = 0.9
    # History repeated to clear _percentile_rank's MIN_PERCENTILE_HISTORY guard;
    # every value still sits below abs(roc)=0.3, so the rank is unchanged at 1.0.
    history = [0.1, 0.2] * 5
    raw_value, band, sub_score, reason = pcr_level_and_roc(current, previous, history, BAND)
    assert raw_value == pytest.approx(0.3)
    assert band == BAND
    assert sub_score == 1.0  # abs(roc)=0.3 >= both history values
    assert "pcr_level" in reason


def test_pcr_direction_agnostic_rising_and_falling_score_equally():
    rising_current = _snapshot(24500.0, 24550.0, [_strike(24500, 1000, 1200)])  # pcr 1.2
    rising_previous = _snapshot(24450.0, 24500.0, [_strike(24500, 1000, 900)])  # pcr 0.9
    falling_current = _snapshot(24500.0, 24550.0, [_strike(24500, 1000, 900)])  # pcr 0.9
    falling_previous = _snapshot(24450.0, 24500.0, [_strike(24500, 1000, 1200)])  # pcr 1.2

    history = [0.1] * MIN_PERCENTILE_HISTORY  # else both sides return a vacuous neutral 0.5
    _, _, rising_score, _ = pcr_level_and_roc(rising_current, rising_previous, history, BAND)
    _, _, falling_score, _ = pcr_level_and_roc(falling_current, falling_previous, history, BAND)
    assert rising_score == falling_score


# --- oi_buildup_classification ---


def test_buildup_first_cycle_no_previous_falls_back():
    current = _snapshot(24500.0, 24550.0, [_strike(24500, 1000, 1200)])
    result = oi_buildup_classification(current, None, BAND)
    assert result == (None, BAND, 0.5, "oi_buildup_classification: no data")


def test_buildup_missing_futures_ltp_falls_back():
    current = _snapshot(24500.0, None, [_strike(24500, 1000, 1200)])
    previous = _snapshot(24450.0, 24500.0, [_strike(24500, 900, 1100)])
    result = oi_buildup_classification(current, previous, BAND)
    assert result[:3] == (None, BAND, 0.5)


@pytest.mark.parametrize(
    "price_up,oi_up,expected",
    [
        (True, True, "long_buildup"),
        (True, False, "short_covering"),
        (False, True, "short_buildup"),
        (False, False, "long_unwinding"),
    ],
)
def test_classify_buildup_all_four_cells(price_up, oi_up, expected):
    assert _classify_buildup(price_up, oi_up) == expected


def test_buildup_price_up_all_oi_up_scores_high():
    # futures rising, both call and put OI rising at the one common strike -> both sides long_buildup
    current = _snapshot(24500.0, 24600.0, [_strike(24500, 1000, 1200)])
    previous = _snapshot(24450.0, 24500.0, [_strike(24500, 800, 900)])
    raw_value, band, sub_score, _ = oi_buildup_classification(current, previous, BAND)
    assert raw_value == 1.0  # every (strike, side) pair in the buildup bucket
    assert sub_score == 1.0
    assert band == BAND


def test_buildup_strike_set_mismatch_excludes_uncommon_strikes():
    # strike 24600 only in current (new listing), strike 24400 only in previous (rolled off)
    current = _snapshot(
        24500.0,
        24600.0,
        [_strike(24500, 1000, 1200), _strike(24600, 500, 500)],
    )
    previous = _snapshot(
        24450.0,
        24500.0,
        [_strike(24500, 800, 900), _strike(24400, 300, 300)],
    )
    raw_value, _, _, _ = oi_buildup_classification(current, previous, BAND)
    # only strike 24500 is common; both sides OI rose while futures rose -> all buildup
    assert raw_value == 1.0


# --- oi_wall_proximity_and_strength ---


def test_wall_proximity_missing_spot_falls_back():
    current = _snapshot(None, 24550.0, [_strike(24500, 1000, 1200)])
    result = oi_wall_proximity_and_strength(current, None, [], [], BAND)
    assert result == (None, BAND, 0.5, "oi_wall_proximity_and_strength: no data")


def test_wall_proximity_realistic_walls_and_distance():
    chain = [
        _strike(24400, 500, 300),
        _strike(24500, 2000, 400),  # call wall (max call_oi)
        _strike(24600, 300, 2500),  # put wall (max put_oi)
    ]
    current = _snapshot(24520.0, 24550.0, chain)
    raw_value, band, sub_score, reason = oi_wall_proximity_and_strength(
        current, None, [], [], BAND
    )
    # nearest wall to spot=24520: call wall @24500 (dist 20) vs put wall @24600 (dist 80)
    expected_proximity = 20 / 24520.0 * 100
    assert raw_value == pytest.approx(expected_proximity)
    assert band == BAND
    assert sub_score == 0.5 * 0.5 + 0.5 * 0.5  # empty histories -> both percentiles neutral


def test_wall_proximity_strength_trend_uses_previous_same_strike():
    chain_current = [
        _strike(24500, 3000, 400),  # call wall, OI grew from 2000 -> 3000
        _strike(24600, 300, 500),
    ]
    chain_previous = [
        _strike(24500, 2000, 400),
        _strike(24600, 300, 500),
    ]
    current = _snapshot(24510.0, 24550.0, chain_current)
    previous = _snapshot(24480.0, 24500.0, chain_previous)
    _, _, sub_score, reason = oi_wall_proximity_and_strength(
        current, previous, [], [1.0], BAND
    )
    assert "strength_trend" in reason


def test_wall_proximity_polarity_far_and_decaying_scores_higher():
    chain = [
        _strike(24500, 3000, 400),
        _strike(24600, 300, 3000),
    ]
    near_spot = _snapshot(24501.0, 24550.0, chain)
    far_spot = _snapshot(25000.0, 24550.0, chain)

    proximity_history = [1.0, 2.0] * 5  # clears MIN_PERCENTILE_HISTORY, same spread
    _, _, near_score, _ = oi_wall_proximity_and_strength(
        near_spot, None, proximity_history, [], BAND
    )
    _, _, far_score, _ = oi_wall_proximity_and_strength(
        far_spot, None, proximity_history, [], BAND
    )
    assert far_score > near_score


# --- max_pain_drift ---


def test_max_pain_drift_no_session_reference_falls_back():
    current = _snapshot(24500.0, 24550.0, [_strike(24500, 1000, 1200)])
    result = max_pain_drift(current, None, [], BAND)
    assert result == (None, BAND, 0.5, "max_pain_drift: no data")


def test_max_pain_hand_computed_symmetric_chain():
    # symmetric chain -> max pain at the middle strike, hand-verified payout table
    chain = [_strike(100, 10, 10), _strike(200, 10, 10), _strike(300, 10, 10)]
    assert _max_pain(chain) == 200


def test_max_pain_drift_realistic_computation():
    chain = [_strike(100, 10, 10), _strike(200, 10, 10), _strike(300, 10, 10)]
    current = _snapshot(200.0, 205.0, chain)
    history = [5.0] * MIN_PERCENTILE_HISTORY  # clears the guard; abs(10) still >= every value
    raw_value, band, sub_score, _ = max_pain_drift(current, 190.0, history, BAND)
    assert raw_value == 10.0  # 200 - 190
    assert band == BAND
    assert sub_score == 1.0  # abs(10) >= history value 5.0


# --- private helpers ---


def test_pcr_zero_call_oi_returns_none():
    assert _pcr([_strike(24500, 0, 1200)]) is None


def test_walls_empty_chain_returns_none():
    assert _walls([]) is None


def test_walls_picks_max_oi_strikes():
    chain = [_strike(24400, 500, 300), _strike(24500, 2000, 400), _strike(24600, 300, 2500)]
    assert _walls(chain) == (24500, 24600)


# --- constraint check: no clock-time branching ---


def test_no_function_takes_a_clock_or_datetime_argument():
    for fn in (
        pcr_level_and_roc,
        oi_buildup_classification,
        oi_wall_proximity_and_strength,
        max_pain_drift,
    ):
        params = inspect.signature(fn).parameters
        for name in params:
            assert "time" not in name.lower() and "clock" not in name.lower(), (
                f"{fn.__name__} takes {name!r} — signals must never branch on wall-clock time"
            )
