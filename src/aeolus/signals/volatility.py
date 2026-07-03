"""Volatility sub-signals (TASK-003, Spec §6.1).

Four pure functions, no I/O, no Supabase/Dhan client — all trailing history is
caller-supplied. Polarity: GO = favorable for directional option buying (see
TASK-003 ADR's polarity table); never copy polarity from a premium-selling
reference tool.
"""

from __future__ import annotations

import math
import statistics

from aeolus.explain.reason import template_reason
from aeolus.ingestion.models import OptionStrike

from .contract import MIN_LOOKBACK_SESSIONS, SignalResult, _percentile_rank


def _atm_strike(option_chain: list[OptionStrike], spot_ltp: float | None) -> OptionStrike | None:
    """Closest strike to spot_ltp. No fallback to a nearby strike on failure."""
    if not option_chain or spot_ltp is None:
        return None
    return min(option_chain, key=lambda s: abs(s.strike - spot_ltp))


def _resolve_atm_iv(option_chain: list[OptionStrike], spot_ltp: float | None) -> float | None:
    """ATM IV = mean of call_iv/put_iv at the closest strike, valid legs only.

    A zero/negative IV leg (bad tick) is dropped, not substituted from a
    nearby strike — resolves to None only if both legs are unusable.
    """
    strike = _atm_strike(option_chain, spot_ltp)
    if strike is None:
        return None
    valid_ivs = [iv for iv in (strike.call_iv, strike.put_iv) if iv > 0]
    if not valid_ivs:
        return None
    return sum(valid_ivs) / len(valid_ivs)


def _realized_vol(trailing_spot_history: list[float]) -> float | None:
    """Annualized close-to-close stdev of log returns, IV-comparable percent scale.

    Range-based estimators (Parkinson/Garman-Klass) aren't feasible — ingestion
    only exposes spot_ltp ticks, never session OHLC bars (TASK-003 ADR).
    """
    if len(trailing_spot_history) < MIN_LOOKBACK_SESSIONS:
        return None
    log_returns = [
        math.log(later / earlier)
        for earlier, later in zip(trailing_spot_history, trailing_spot_history[1:])
        if earlier > 0 and later > 0
    ]
    if len(log_returns) < 2:
        return None
    return statistics.stdev(log_returns) * math.sqrt(252) * 100


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def iv_percentile_rank(
    current_iv: float | None,
    trailing_iv_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """20-60d percentile rank of current_iv within trailing_iv_history.

    Low IV percentile = complacent/quiet = NO-GO; higher percentile -> higher
    score (inverse of a premium-selling tool's polarity).
    None / <20 sessions of history -> (None, reference_band, 0.5, reason).
    """
    name = "iv_percentile_rank"
    if current_iv is None or len(trailing_iv_history) < MIN_LOOKBACK_SESSIONS:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    sub_score = _percentile_rank(current_iv, trailing_iv_history)
    reason = template_reason(name, current_iv, reference_band, sub_score)
    return (current_iv, reference_band, sub_score, reason)


def iv_rv_spread(
    current_iv: float | None,
    previous_iv: float | None,
    trailing_spot_history: list[float],
    trailing_iv_rising_magnitude_history: list[float],
    trailing_iv_falling_magnitude_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """raw_value = current_iv - previous_iv (IV's own trend, dominant driver
    per human field feedback — falling IV is NO-GO-favorable regardless of
    absolute level). RV spread (current_iv - realized_vol) still surfaced via
    template_reason's context param, no longer drives sub_score.
    previous_iv is None (first cycle) -> (None, reference_band, 0.5, reason).
    """
    name = "iv_rv_spread"
    if current_iv is None or previous_iv is None:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    iv_trend = current_iv - previous_iv
    if iv_trend > 0:
        sub_score = 0.5 + 0.5 * _percentile_rank(iv_trend, trailing_iv_rising_magnitude_history)
    elif iv_trend < 0:
        sub_score = 0.5 - 0.5 * _percentile_rank(
            abs(iv_trend), trailing_iv_falling_magnitude_history
        )
    else:
        sub_score = 0.5

    context = None
    realized_vol = _realized_vol(trailing_spot_history)
    if realized_vol is not None:
        context = {"iv_minus_rv_spread": current_iv - realized_vol}

    reason = template_reason(name, iv_trend, reference_band, sub_score, context=context)
    return (iv_trend, reference_band, sub_score, reason)


def vix_level_and_roc(
    current_vix: float | None,
    trailing_vix_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """VIX level + rate of change over trailing_vix_history.

    Elevated/rising VIX -> higher score (same direction as IV percentile).
    current_vix is None whenever IngestionSnapshot has no VIX value ->
    (None, reference_band, 0.5, reason).
    """
    name = "vix_level_and_roc"
    if current_vix is None:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    level_score = _percentile_rank(current_vix, trailing_vix_history)
    roc = current_vix - trailing_vix_history[-1] if trailing_vix_history else 0.0
    roc_history = [
        later - earlier for earlier, later in zip(trailing_vix_history, trailing_vix_history[1:])
    ]
    roc_score = _percentile_rank(roc, roc_history)
    sub_score = 0.5 * level_score + 0.5 * roc_score

    reason = template_reason(name, current_vix, reference_band, sub_score, context={"roc": roc})
    return (current_vix, reference_band, sub_score, reason)


def expected_move_consumed_ratio(
    spot_ltp: float | None,
    session_reference_price: float | None,
    straddle_implied_expected_move: float | None,
    reference_band: tuple[float, float],
) -> SignalResult:
    """abs(spot_ltp - session_reference_price) / straddle_implied_expected_move.

    Realized move meeting/exceeding priced-in move -> higher score; a low
    ratio late in session is the clearest single quiet/pinned tell.
    Live-only. Any None input, or a zero expected move, ->
    (None, reference_band, 0.5, reason).
    """
    name = "expected_move_consumed_ratio"
    if (
        spot_ltp is None
        or session_reference_price is None
        or straddle_implied_expected_move is None
        or straddle_implied_expected_move == 0
    ):
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    raw_value = abs(spot_ltp - session_reference_price) / straddle_implied_expected_move
    low, high = reference_band
    sub_score = _clamp01((raw_value - low) / (high - low)) if high != low else 0.5

    reason = template_reason(name, raw_value, reference_band, sub_score)
    return (raw_value, reference_band, sub_score, reason)
