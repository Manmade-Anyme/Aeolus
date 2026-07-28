"""Gamma sub-signals (TASK-004, Spec §6.2).

Two pure functions, no I/O — consume only option_chain + spot_ltp from
IngestionSnapshot. Polarity: GO = favorable for directional option buying.
"""

from __future__ import annotations

import logging

from aeolus.explain.reason import template_reason
from aeolus.ingestion.models import OptionStrike

from .contract import SignalResult, _clamp01, _percentile_rank

logger = logging.getLogger(__name__)


def _net_gamma_by_strike(option_chain: list[OptionStrike]) -> list[tuple[float, float]]:
    """(strike, net_gamma_at_strike) pairs, sorted ascending by strike.

    net_gamma_at_strike = call_gamma * call_oi - put_gamma * put_oi. Dealer is
    long gamma on calls sold to them, short gamma on puts sold to them.
    Strikes with non-zero OI but zeroed gamma are excluded to avoid under-counting GEX.
    """
    valid_strikes: list[OptionStrike] = []
    excluded_count = 0
    for s in option_chain:
        if s.has_valid_greeks:
            valid_strikes.append(s)
        else:
            excluded_count += 1

    if excluded_count > 0:
        logger.warning(
            "Excluded %d strike(s) with non-zero OI but zeroed gamma from GEX calculation",
            excluded_count,
        )

    return sorted(
        (
            (s.strike, s.call_greeks.gamma * s.call_oi - s.put_greeks.gamma * s.put_oi)
            for s in valid_strikes
        ),
        key=lambda pair: pair[0],
    )



def _flip_level(net_gamma_by_strike: list[tuple[float, float]]) -> float | None:
    """Strike where cumulative net_gamma_at_strike changes sign, linearly
    interpolated between the two bracketing strikes. None if the chain-order
    cumulative sum never crosses zero (monotonic) — never extrapolated
    beyond the traded strike range.
    """
    if len(net_gamma_by_strike) < 2:
        return None

    cumulative: list[tuple[float, float]] = []
    running_total = 0.0
    for strike, net_gamma in net_gamma_by_strike:
        running_total += net_gamma
        cumulative.append((strike, running_total))

    for (strike_a, cum_a), (strike_b, cum_b) in zip(cumulative, cumulative[1:]):
        if cum_a == 0:
            return strike_a
        if (cum_a < 0) != (cum_b < 0):
            fraction = -cum_a / (cum_b - cum_a)
            return strike_a + (strike_b - strike_a) * fraction

    if cumulative[-1][1] == 0:
        return cumulative[-1][0]
    return None


def _total_oi(option_chain: list[OptionStrike]) -> int:
    return sum(s.call_oi + s.put_oi for s in option_chain)


def gex_regime(
    option_chain: list[OptionStrike],
    spot_ltp: float | None,
    lot_size: int,
    trailing_gex_magnitude_history: list[float],
    reference_band: tuple[float, float],
    min_total_oi: int = 0,
) -> SignalResult:
    """Signed, dollar-scaled net GEX across the chain.

    Negative net GEX -> dealers net short gamma -> hedge with price moves ->
    amplifying/trending regime -> GO-favorable. Positive -> dampening/pinning
    regime -> NO-GO-favorable. Magnitude normalized via shared
    _percentile_rank against trailing history — weak and strong readings of
    the same sign score differently.
    Missing spot_ltp / empty chain / thin OI -> (None, reference_band, 0.5, reason).
    """
    name = "gex_regime"
    if spot_ltp is None or not option_chain or _total_oi(option_chain) < min_total_oi:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    net_gamma_by_strike = _net_gamma_by_strike(option_chain)
    net_gamma_sum = sum(ng for _, ng in net_gamma_by_strike)
    raw_value = net_gamma_sum * spot_ltp**2 * lot_size * 0.01

    magnitude_pct = _percentile_rank(abs(raw_value), trailing_gex_magnitude_history)
    sub_score = 0.5 + 0.5 * magnitude_pct if raw_value < 0 else 0.5 - 0.5 * magnitude_pct

    reason = template_reason(name, raw_value, reference_band, sub_score)
    return (raw_value, reference_band, sub_score, reason)


def spot_distance_from_flip(
    option_chain: list[OptionStrike],
    spot_ltp: float | None,
    reference_band: tuple[float, float],
    min_total_oi: int = 0,
) -> SignalResult:
    """Percent distance of spot_ltp from the interpolated zero-gamma flip strike
    (strike-domain proxy, not BS-recentered).

    sub_score driven by |distance| only, sign-independent — near the flip
    level is the ambiguous pin/magnet zone (NO-GO-favorable), well clear of
    it in either direction means the current regime is more likely to hold
    (GO-favorable).
    No sign-changing crossing found in chain, missing spot_ltp, empty chain,
    or thin OI -> (None, reference_band, 0.5, reason).
    """
    name = "spot_distance_from_flip"
    if spot_ltp is None or not option_chain or _total_oi(option_chain) < min_total_oi:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    flip_level = _flip_level(_net_gamma_by_strike(option_chain))
    if flip_level is None:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    raw_value = (spot_ltp - flip_level) / spot_ltp * 100
    low, high = reference_band
    sub_score = _clamp01((abs(raw_value) - low) / (high - low)) if high != low else 0.5

    reason = template_reason(name, raw_value, reference_band, sub_score)
    return (raw_value, reference_band, sub_score, reason)
