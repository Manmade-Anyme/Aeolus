import inspect

from aeolus.ingestion.models import Greeks, OptionStrike
from aeolus.signals.volatility import (
    _atm_strike,
    _resolve_atm_iv,
    expected_move_consumed_ratio,
    iv_percentile_rank,
    iv_rv_spread,
    vix_level_and_roc,
)

BAND = (20.0, 80.0)


def _greeks() -> Greeks:
    return Greeks(delta=0.5, gamma=0.01, theta=-2.0, vega=1.2)


def _strike(strike: float, call_iv: float, put_iv: float) -> OptionStrike:
    return OptionStrike(
        strike=strike,
        call_oi=1000,
        put_oi=1200,
        call_iv=call_iv,
        put_iv=put_iv,
        call_greeks=_greeks(),
        put_greeks=_greeks(),
    )


# --- iv_percentile_rank ---


def test_iv_percentile_rank_insufficient_history_falls_back():
    result = iv_percentile_rank(25.0, [10.0] * 19, BAND)
    assert result == (None, BAND, 0.5, "iv_percentile_rank: no data")


def test_iv_percentile_rank_missing_current_iv_falls_back():
    result = iv_percentile_rank(None, [float(v) for v in range(10, 30)], BAND)
    assert result[:3] == (None, BAND, 0.5)


def test_iv_percentile_rank_polarity_higher_percentile_higher_score():
    history = [float(v) for v in range(10, 30)]  # 20 sessions, 10..29
    _, _, low_score, _ = iv_percentile_rank(10.5, history, BAND)
    _, _, high_score, _ = iv_percentile_rank(29.0, history, BAND)
    assert high_score > low_score


def test_iv_percentile_rank_realistic_value():
    history = [float(v) for v in range(10, 30)]
    raw, band, sub_score, reason = iv_percentile_rank(25.0, history, BAND)
    assert raw == 25.0
    assert band == BAND
    assert sub_score == 16 / 20  # 16 of 20 values <= 25.0
    assert "iv_percentile_rank" in reason


# --- iv_rv_spread ---


def test_iv_rv_spread_first_cycle_no_previous_falls_back():
    result = iv_rv_spread(15.0, None, [], [], [], BAND)
    assert result == (None, BAND, 0.5, "iv_rv_spread: no data")


def test_iv_rv_spread_polarity_rising_vs_falling_iv():
    rising_history = [1.0, 2.0, 3.0]
    falling_history = [1.0, 2.0, 3.0]

    _, _, rising_score, _ = iv_rv_spread(16.0, 14.0, [], rising_history, falling_history, BAND)
    _, _, falling_score, _ = iv_rv_spread(14.0, 16.0, [], rising_history, falling_history, BAND)
    _, _, flat_score, _ = iv_rv_spread(15.0, 15.0, [], rising_history, falling_history, BAND)

    assert rising_score > 0.5 > falling_score
    assert flat_score == 0.5


def test_iv_rv_spread_raw_value_is_signed_iv_trend_not_spread_level():
    raw_value, _, _, _ = iv_rv_spread(16.0, 14.0, [], [1.0, 2.0], [1.0, 2.0], BAND)
    assert raw_value == 2.0  # current_iv - previous_iv, not iv - realized_vol


def test_iv_rv_spread_context_carries_rv_spread_when_history_sufficient():
    spot_history = [24000.0 + i * 5 for i in range(21)]
    _, _, _, reason = iv_rv_spread(16.0, 14.0, spot_history, [1.0], [1.0], BAND)
    assert "iv_minus_rv_spread" in reason


def test_iv_rv_spread_omits_context_when_spot_history_too_short():
    _, _, _, reason = iv_rv_spread(16.0, 14.0, [24000.0, 24010.0], [1.0], [1.0], BAND)
    assert "iv_minus_rv_spread" not in reason


# --- vix_level_and_roc ---


def test_vix_level_and_roc_missing_vix_falls_back_end_to_end():
    result = vix_level_and_roc(None, [12.0, 13.0, 14.0], BAND)
    assert result == (None, BAND, 0.5, "vix_level_and_roc: no data")


def test_vix_level_and_roc_empty_history_does_not_crash():
    raw, band, sub_score, reason = vix_level_and_roc(15.0, [], BAND)
    assert raw == 15.0
    assert band == BAND
    assert sub_score == 0.5  # no basis for comparison, neutral fallback
    assert "vix_level_and_roc" in reason


def test_vix_level_and_roc_polarity_elevated_rising_scores_higher():
    history = [12.0, 12.5, 13.0, 13.5, 14.0]
    _, _, elevated_rising_score, _ = vix_level_and_roc(20.0, history, BAND)
    _, _, quiet_falling_score, _ = vix_level_and_roc(10.0, history, BAND)
    assert elevated_rising_score > quiet_falling_score


# --- expected_move_consumed_ratio ---


def test_expected_move_consumed_ratio_missing_input_falls_back():
    result = expected_move_consumed_ratio(None, 24500.0, 150.0, (0.3, 1.0))
    assert result == (None, (0.3, 1.0), 0.5, "expected_move_consumed_ratio: no data")


def test_expected_move_consumed_ratio_zero_expected_move_falls_back():
    result = expected_move_consumed_ratio(24500.0, 24500.0, 0.0, (0.3, 1.0))
    assert result[:3] == (None, (0.3, 1.0), 0.5)


def test_expected_move_consumed_ratio_polarity_more_move_delivered_higher_score():
    band = (0.3, 1.0)
    _, _, low_score, _ = expected_move_consumed_ratio(24510.0, 24500.0, 150.0, band)
    _, _, high_score, _ = expected_move_consumed_ratio(24650.0, 24500.0, 150.0, band)
    assert high_score > low_score


def test_expected_move_consumed_ratio_raw_value_computation():
    raw_value, _, sub_score, _ = expected_move_consumed_ratio(24650.0, 24500.0, 150.0, (0.3, 1.0))
    assert raw_value == 1.0
    assert sub_score == 1.0  # at/above band high, clamped


# --- private helpers ---


def test_atm_strike_picks_closest_strike():
    chain = [_strike(24400, 14.0, 14.2), _strike(24500, 13.0, 13.1), _strike(24600, 14.5, 14.6)]
    assert _atm_strike(chain, 24510.0).strike == 24500


def test_atm_strike_empty_chain_returns_none():
    assert _atm_strike([], 24500.0) is None


def test_resolve_atm_iv_averages_valid_legs():
    chain = [_strike(24500, 14.0, 14.4)]
    assert _resolve_atm_iv(chain, 24500.0) == 14.2


def test_resolve_atm_iv_drops_bad_leg_no_fallback_to_nearby_strike():
    chain = [_strike(24400, 14.0, 14.2), _strike(24500, -1.0, 0.0)]
    assert _resolve_atm_iv(chain, 24500.0) is None


# --- constraint check: no clock-time branching ---


def test_no_function_takes_a_clock_or_datetime_argument():
    for fn in (
        iv_percentile_rank,
        iv_rv_spread,
        vix_level_and_roc,
        expected_move_consumed_ratio,
    ):
        params = inspect.signature(fn).parameters
        for name in params:
            assert "time" not in name.lower() and "clock" not in name.lower(), (
                f"{fn.__name__} takes {name!r} — signals must never branch on wall-clock time"
            )
