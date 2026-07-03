"""OI structure sub-signals (TASK-005, Spec §6.3).

Four pure functions, no I/O. Unlike TASK-003/004, three of these four are
cycle-relative: "previous" means the immediately preceding computation
cycle's IngestionSnapshot, whatever cadence the caller (scheduler) actually
runs at — never a fixed wall-clock duration computed inside this module.
"""

from __future__ import annotations

from aeolus.explain.reason import template_reason
from aeolus.ingestion.models import IngestionSnapshot, OptionStrike

from .contract import SignalResult, _percentile_rank


def _pcr(option_chain: list[OptionStrike]) -> float | None:
    """Put-call OI ratio. None if the chain is empty or has zero call OI."""
    total_call_oi = sum(s.call_oi for s in option_chain)
    if total_call_oi == 0:
        return None
    total_put_oi = sum(s.put_oi for s in option_chain)
    return total_put_oi / total_call_oi


def _classify_buildup(price_up: bool, oi_up: bool) -> str:
    """Standard futures-style joint price+OI read."""
    if price_up and oi_up:
        return "long_buildup"
    if price_up and not oi_up:
        return "short_covering"
    if not price_up and oi_up:
        return "short_buildup"
    return "long_unwinding"


def _walls(option_chain: list[OptionStrike]) -> tuple[float, float] | None:
    """(call_wall_strike, put_wall_strike) — strikes of max call_oi / max put_oi."""
    if not option_chain:
        return None
    call_wall = max(option_chain, key=lambda s: s.call_oi)
    put_wall = max(option_chain, key=lambda s: s.put_oi)
    return call_wall.strike, put_wall.strike


def _max_pain(option_chain: list[OptionStrike]) -> float | None:
    """Strike minimizing total option-writer payout. No interpolation —
    always an actual listed strike."""
    if not option_chain:
        return None
    best_strike: float | None = None
    best_payout: float | None = None
    for candidate in option_chain:
        payout = sum(
            s.call_oi * max(0.0, candidate.strike - s.strike)
            + s.put_oi * max(0.0, s.strike - candidate.strike)
            for s in option_chain
        )
        if best_payout is None or payout < best_payout:
            best_payout = payout
            best_strike = candidate.strike
    return best_strike


def pcr_level_and_roc(
    current: IngestionSnapshot,
    previous: IngestionSnapshot | None,
    trailing_pcr_roc_magnitude_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """raw_value = pcr_now - pcr_prev (rate of change, not level).

    Direction-agnostic: fast-moving PCR either direction -> higher score
    (repositioning happening), flat PCR -> neutral (complacency).
    previous is None, or PCR undefined for either cycle (zero call OI) ->
    (None, reference_band, 0.5, reason).
    """
    name = "pcr_level_and_roc"
    if previous is None:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    pcr_now = _pcr(current.option_chain)
    pcr_prev = _pcr(previous.option_chain)
    if pcr_now is None or pcr_prev is None:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    roc = pcr_now - pcr_prev
    sub_score = 0.5 + 0.5 * _percentile_rank(abs(roc), trailing_pcr_roc_magnitude_history)
    reason = template_reason(name, roc, reference_band, sub_score, context={"pcr_level": pcr_now})
    return (roc, reference_band, sub_score, reason)


def oi_buildup_classification(
    current: IngestionSnapshot,
    previous: IngestionSnapshot | None,
    reference_band: tuple[float, float],
) -> SignalResult:
    """OI-weighted fraction of (strike, side) pairs in {long_buildup, short_buildup}
    among strikes present in both snapshots.

    Price-direction signal is futures_ltp (current vs previous), the
    standard NSE F&O "buildup" convention — not spot, and not per-option
    premium (OptionStrike has no call_ltp/put_ltp field). Strikes present
    in only one snapshot are skipped, not fabricated.
    previous is None, or futures_ltp missing on either side ->
    (None, reference_band, 0.5, reason).
    """
    name = "oi_buildup_classification"
    if previous is None or current.futures_ltp is None or previous.futures_ltp is None:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    price_up = current.futures_ltp > previous.futures_ltp

    prev_by_strike = {s.strike: s for s in previous.option_chain}
    buildup_weight = 0.0
    total_weight = 0.0
    for curr_strike in current.option_chain:
        prev_strike = prev_by_strike.get(curr_strike.strike)
        if prev_strike is None:
            continue
        for oi_now, oi_prev in (
            (curr_strike.call_oi, prev_strike.call_oi),
            (curr_strike.put_oi, prev_strike.put_oi),
        ):
            classification = _classify_buildup(price_up, oi_now > oi_prev)
            total_weight += oi_now
            if classification in ("long_buildup", "short_buildup"):
                buildup_weight += oi_now

    if total_weight == 0:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    raw_value = buildup_weight / total_weight
    sub_score = raw_value
    reason = template_reason(name, raw_value, reference_band, sub_score)
    return (raw_value, reference_band, sub_score, reason)


def oi_wall_proximity_and_strength(
    current: IngestionSnapshot,
    previous: IngestionSnapshot | None,
    trailing_wall_proximity_history: list[float],
    trailing_wall_strength_trend_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """raw_value = percent distance to nearest of {max-call-OI strike, max-put-OI
    strike}. sub_score blends proximity + strength-trend percentiles 50/50.

    Close + strengthening = pin risk = NO-GO-favorable; far or decaying =
    GO-favorable. Mirrors TASK-004's spot_distance_from_flip design language.
    Missing spot_ltp / empty chain -> (None, reference_band, 0.5, reason).
    """
    name = "oi_wall_proximity_and_strength"
    if current.spot_ltp is None or not current.option_chain:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    walls = _walls(current.option_chain)
    assert walls is not None  # option_chain non-empty, checked above
    call_wall_strike, put_wall_strike = walls
    spot = current.spot_ltp

    call_distance = abs(call_wall_strike - spot)
    put_distance = abs(spot - put_wall_strike)
    if call_distance <= put_distance:
        nearest_strike, nearest_side, nearest_distance = call_wall_strike, "call", call_distance
    else:
        nearest_strike, nearest_side, nearest_distance = put_wall_strike, "put", put_distance
    proximity_pct = nearest_distance / spot * 100

    strength_trend: float | None = None
    if previous is not None:
        prev_strike = next(
            (s for s in previous.option_chain if s.strike == nearest_strike), None
        )
        if prev_strike is not None:
            wall_oi_prev = prev_strike.call_oi if nearest_side == "call" else prev_strike.put_oi
            if wall_oi_prev != 0:
                curr_strike = next(
                    s for s in current.option_chain if s.strike == nearest_strike
                )
                wall_oi_now = (
                    curr_strike.call_oi if nearest_side == "call" else curr_strike.put_oi
                )
                strength_trend = (wall_oi_now - wall_oi_prev) / wall_oi_prev

    proximity_score = _percentile_rank(proximity_pct, trailing_wall_proximity_history)
    strength_score = (
        _percentile_rank(-strength_trend, trailing_wall_strength_trend_history)
        if strength_trend is not None
        else 0.5
    )
    sub_score = 0.5 * proximity_score + 0.5 * strength_score

    context = {"strength_trend": strength_trend} if strength_trend is not None else None
    reason = template_reason(name, proximity_pct, reference_band, sub_score, context=context)
    return (proximity_pct, reference_band, sub_score, reason)


def max_pain_drift(
    current: IngestionSnapshot,
    session_open_max_pain: float | None,
    trailing_max_pain_drift_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """raw_value = current_max_pain - session_open_max_pain.

    Direction-agnostic movement-is-informative shape, same as PCR ROC.
    session_open_max_pain is None, or chain empty ->
    (None, reference_band, 0.5, reason).
    """
    name = "max_pain_drift"
    if session_open_max_pain is None or not current.option_chain:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    current_max_pain = _max_pain(current.option_chain)
    assert current_max_pain is not None  # option_chain non-empty, checked above

    raw_value = current_max_pain - session_open_max_pain
    sub_score = 0.5 + 0.5 * _percentile_rank(abs(raw_value), trailing_max_pain_drift_history)
    reason = template_reason(name, raw_value, reference_band, sub_score)
    return (raw_value, reference_band, sub_score, reason)
