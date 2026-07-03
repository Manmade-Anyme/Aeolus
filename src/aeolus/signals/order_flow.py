"""Order flow sub-signals (TASK-006, Spec §6.4).

Three pure functions, no I/O, no internal state. cvd_delta_history,
price_history, and established_range are all session-scoped state the
caller accumulates/holds across cycles — this module never fetches,
caches, or resets anything itself, same discipline as TASK-003..005.
"""

from __future__ import annotations

from aeolus.explain.reason import template_reason
from aeolus.ingestion.models import IngestionSnapshot

from .contract import SignalResult, _clamp01, _percentile_rank


def _tick_classify(current: IngestionSnapshot, previous: IngestionSnapshot | None) -> float | None:
    """Signed volume_delta for one cycle: attributed to the side futures_ltp
    moved, per Dhan's own cumulative session volume counter.

    A flat tick (unchanged futures_ltp) attributes zero, never split 50/50 --
    avoids fabricating a lean. None if either snapshot is missing volume/price.
    """
    if (
        previous is None
        or current.volume is None
        or previous.volume is None
        or current.futures_ltp is None
        or previous.futures_ltp is None
    ):
        return None

    volume_delta = float(current.volume - previous.volume)
    if current.futures_ltp > previous.futures_ltp:
        return volume_delta
    if current.futures_ltp < previous.futures_ltp:
        return -volume_delta
    return 0.0


def _near_extreme(
    price: float, day_low: float, day_high: float, extreme_threshold: float
) -> tuple[bool, bool]:
    """(near_low, near_high) — price within extreme_threshold (fraction of
    today's range) of either end. Zero-width range -> (False, False), never
    a division by zero."""
    range_span = day_high - day_low
    if range_span <= 0:
        return False, False
    near_low = (price - day_low) / range_span < extreme_threshold
    near_high = (day_high - price) / range_span < extreme_threshold
    return near_low, near_high


def cvd_direction_and_divergence(
    cvd_delta_history: list[float],
    price_history: list[float],
    trailing_cvd_magnitude_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """raw_value = cvd_trend = sum(cvd_delta_history).

    Confirming (price and CVD trend the same direction) -> GO-favorable;
    diverging (price moved, CVD didn't agree) -> NO-GO-favorable ("price
    progress without CVD confirmation is a fragile move"). reference_band's
    low bound is the flat-threshold gate on |price_trend|, not a band on
    cvd_trend itself -- price_trend below it means nothing to confirm or
    diverge from.
    Empty/single-point history -> (None, reference_band, 0.5, reason).
    """
    name = "cvd_direction_and_divergence"
    if not cvd_delta_history or len(price_history) < 2:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    cvd_trend = sum(cvd_delta_history)
    price_trend = price_history[-1] - price_history[0]
    flat_threshold = reference_band[0]

    if abs(price_trend) < flat_threshold:
        sub_score = 0.5
    else:
        confirming = (price_trend > 0 and cvd_trend > 0) or (price_trend < 0 and cvd_trend < 0)
        magnitude_pct = _percentile_rank(abs(cvd_trend), trailing_cvd_magnitude_history)
        sub_score = 0.5 + 0.5 * magnitude_pct if confirming else 0.5 - 0.5 * magnitude_pct

    reason = template_reason(
        name, cvd_trend, reference_band, sub_score, context={"price_trend": price_trend}
    )
    return (cvd_trend, reference_band, sub_score, reason)


def delta_imbalance_and_absorption(
    current: IngestionSnapshot,
    trailing_imbalance_magnitude_history: list[float],
    extreme_threshold: float,
    reference_band: tuple[float, float],
) -> SignalResult:
    """raw_value = (total_buy_quantity - total_sell_quantity) / total.

    Confirms an extreme (sell-heavy at the low / buy-heavy at the high) ->
    GO-favorable, the imbalance agrees with a developing break. Opposes it
    (absorption: buy-heavy at the low / sell-heavy at the high) ->
    NO-GO-favorable, resting orders defending the extreme, range likely
    holds (human-confirmed polarity, 2026-07-03). Not near either extreme ->
    weaker baseline lean, capped below the at-extreme conviction ceiling.
    Zero book, missing inputs, or zero-width day range ->
    (None, reference_band, 0.5, reason).
    """
    name = "delta_imbalance_and_absorption"
    if (
        current.total_buy_quantity is None
        or current.total_sell_quantity is None
        or current.futures_ltp is None
        or current.day_high is None
        or current.day_low is None
    ):
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    total = current.total_buy_quantity + current.total_sell_quantity
    if total == 0 or current.day_high - current.day_low <= 0:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    imbalance = (current.total_buy_quantity - current.total_sell_quantity) / total
    near_low, near_high = _near_extreme(
        current.futures_ltp, current.day_low, current.day_high, extreme_threshold
    )
    magnitude_pct = _percentile_rank(abs(imbalance), trailing_imbalance_magnitude_history)

    if not near_low and not near_high:
        sub_score = 0.5 + 0.25 * magnitude_pct
    else:
        confirms_extreme = (near_low and imbalance < 0) or (near_high and imbalance > 0)
        sub_score = 0.5 + 0.5 * magnitude_pct if confirms_extreme else 0.5 - 0.5 * magnitude_pct

    reason = template_reason(name, imbalance, reference_band, sub_score)
    return (imbalance, reference_band, sub_score, reason)


def volume_participation_range(
    current: IngestionSnapshot,
    established_range: tuple[float, float] | None,
    average_daily_volume: float,
    volume_participation_pct: float,
    min_range_width: float,
    trailing_excursion_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """Establishes (day_low, day_high) as the session's participation-window
    range the first cycle cumulative volume crosses
    volume_participation_pct * average_daily_volume AND the range is at
    least min_range_width wide (degenerate-range mitigation). Held by the
    caller thereafter and passed back in as established_range every cycle
    -- this function never re-derives it once set, but does surface the
    established bounds via the reason string's context every cycle so the
    caller has a concrete value to persist (the SignalResult 4-tuple has no
    other channel for this session-scoped fact).

    raw_value = 0 while price holds inside the established range (GO-averse,
    compression); signed excursion beyond the nearer edge once broken
    (GO-favorable, magnitude-scaled). Not yet established, or missing
    volume/day_high/day_low/futures_ltp -> (None, reference_band, 0.5, reason).
    """
    name = "volume_participation_range"
    if (
        current.volume is None
        or current.day_high is None
        or current.day_low is None
        or current.futures_ltp is None
    ):
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    if established_range is None:
        threshold_volume = volume_participation_pct * average_daily_volume
        range_width = current.day_high - current.day_low
        if current.volume >= threshold_volume and range_width >= min_range_width:
            established_range = (current.day_low, current.day_high)
        else:
            reason = template_reason(name, None, reference_band, 0.5)
            return (None, reference_band, 0.5, reason)

    low, high = established_range
    price = current.futures_ltp

    if low <= price <= high:
        raw_value = 0.0
        range_width = high - low
        if range_width > 0:
            distance_from_nearest_edge = min(price - low, high - price)
            inside_pct = _clamp01(distance_from_nearest_edge / (range_width / 2))
        else:
            inside_pct = 1.0
        sub_score = 0.5 - 0.5 * inside_pct
    else:
        raw_value = price - high if price > high else price - low
        sub_score = 0.5 + 0.5 * _percentile_rank(abs(raw_value), trailing_excursion_history)

    context = {"established_low": low, "established_high": high}
    reason = template_reason(name, raw_value, reference_band, sub_score, context=context)
    return (raw_value, reference_band, sub_score, reason)
