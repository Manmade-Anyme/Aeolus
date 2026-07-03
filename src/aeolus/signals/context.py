"""Context sub-signals (TASK-007, Spec §6.5).

Five pure functions, no I/O, no internal state -- cycle_price_volume_history,
basis_history, price_history, and every prior_* / trailing_*_history input
are session-scoped or cross-session state the caller accumulates/seeds
(from the previous session_date's final signal_snapshots row), same
discipline as TASK-003..006.

dte() is deliberately NOT on the standard SignalResult contract -- per
OPEN_DECISIONS #2, DTE routes TASK-008's config-table selection, it is not
itself a scored composite input.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from aeolus.explain.reason import template_reason

from .contract import SignalResult, _percentile_rank


class DayProfileShape(str, Enum):
    TREND = "trend"
    BALANCED = "balanced"


def dte(session_date: date, expiry_date: date | None) -> int | None:
    """Days to the nearest NIFTY option expiry.

    expiry_date comes from IngestionSnapshot, itself sourced from Dhan's own
    expiry_list endpoint (already holiday-shift aware) -- this function does
    not touch a calendar. None in -> None out.
    """
    if expiry_date is None:
        return None
    return (expiry_date - session_date).days


def build_volume_price_histogram(
    cycle_price_volume_history: list[tuple[float, float]],
    bucket_size: float,
) -> dict[float, float]:
    """Cycle-sampled approximation of a volume-at-price distribution.

    cycle_price_volume_history is (futures_ltp, volume_delta) per cycle,
    session-scoped, caller-held (same shape as TASK-006's
    cvd_delta_history/price_history). volume_delta is Dhan's own cumulative
    session counter delta -- a WS reconnect produces one oversized bucket
    contribution for the spanning cycle, not a double-count or reset, same
    reasoning TASK-006 already established.
    """
    histogram: dict[float, float] = {}
    for price, volume_delta in cycle_price_volume_history:
        bucket = round(price / bucket_size) * bucket_size
        histogram[bucket] = histogram.get(bucket, 0.0) + volume_delta
    return histogram


def value_area(
    histogram: dict[float, float],
    area_pct: float,
) -> tuple[float, float, float] | None:
    """(poc, va_low, va_high) -- POC is the max-volume bucket; the value
    area expands outward from it, one adjacent bucket at a time (always
    the heavier of the two available neighbors), until cumulative volume
    share >= area_pct. None if histogram is empty (no prior-day data yet).
    """
    if not histogram:
        return None

    sorted_prices = sorted(histogram)
    poc = max(histogram, key=lambda price: histogram[price])
    poc_idx = sorted_prices.index(poc)
    total_volume = sum(histogram.values())
    target = area_pct * total_volume

    low_idx = high_idx = poc_idx
    accumulated = histogram[poc]

    while accumulated < target and (low_idx > 0 or high_idx < len(sorted_prices) - 1):
        next_low_volume = histogram[sorted_prices[low_idx - 1]] if low_idx > 0 else -1.0
        next_high_volume = (
            histogram[sorted_prices[high_idx + 1]] if high_idx < len(sorted_prices) - 1 else -1.0
        )
        if next_low_volume >= next_high_volume:
            low_idx -= 1
            accumulated += next_low_volume
        else:
            high_idx += 1
            accumulated += next_high_volume

    return poc, sorted_prices[low_idx], sorted_prices[high_idx]


def prior_day_profile_shape(
    prior_day_high: float | None,
    prior_day_low: float | None,
    prior_close: float | None,
    prior_value_area: tuple[float, float, float] | None,
    trailing_average_range_history: list[float],
    expansion_threshold: float,
    extreme_threshold: float,
    reference_band: tuple[float, float],
) -> tuple[DayProfileShape | None, SignalResult]:
    """Trend day vs balanced/rotational, approximated without true TPO data:
    range expanded beyond the recent norm AND the session closed near an
    extreme (one directional push held into the close) -> TREND. Otherwise
    -> BALANCED. value_area_width_ratio is computed and surfaced via the
    reason string as a reinforcing data point only, not a second gate.

    Polarity (human-confirmed 2026-07-03): TREND -> GO-favorable, BALANCED
    -> NO-GO-favorable, consistent with the "quiet/pinned = NO-GO, movement
    = GO" theme across every module in this suite. Missing inputs or a
    zero-width prior day range -> (None, (None, reference_band, 0.5, reason)).
    """
    name = "prior_day_profile_shape"
    if (
        prior_day_high is None
        or prior_day_low is None
        or prior_close is None
        or prior_value_area is None
        or not trailing_average_range_history
    ):
        reason = template_reason(name, None, reference_band, 0.5)
        return None, (None, reference_band, 0.5, reason)

    day_range = prior_day_high - prior_day_low
    trailing_average_range = sum(trailing_average_range_history) / len(
        trailing_average_range_history
    )
    if day_range <= 0 or trailing_average_range <= 0:
        reason = template_reason(name, None, reference_band, 0.5)
        return None, (None, reference_band, 0.5, reason)

    range_expansion = day_range / trailing_average_range
    close_location = (prior_close - prior_day_low) / day_range
    _, va_low, va_high = prior_value_area
    value_area_width_ratio = (va_high - va_low) / day_range

    is_trend_day = range_expansion > expansion_threshold and (
        close_location > extreme_threshold or close_location < (1 - extreme_threshold)
    )
    magnitude_pct = _percentile_rank(day_range, trailing_average_range_history)

    if is_trend_day:
        shape = DayProfileShape.TREND
        sub_score = 0.5 + 0.5 * magnitude_pct
    else:
        shape = DayProfileShape.BALANCED
        sub_score = 0.5 - 0.5 * magnitude_pct

    reason = template_reason(
        name,
        range_expansion,
        reference_band,
        sub_score,
        context={
            "close_location": close_location,
            "value_area_width_ratio": value_area_width_ratio,
        },
    )
    return shape, (range_expansion, reference_band, sub_score, reason)


def gap_classification(
    session_open: float | None,
    current_futures_ltp: float | None,
    prior_value_area: tuple[float, float, float] | None,
    prior_close: float | None,
    trailing_gap_magnitude_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """no_gap / gap_and_go / gap_and_fill vs yesterday's value area.

    session_open is the caller-captured first non-None futures_ltp of the
    session -- deliberately not the Full packet's still-unverified `open`
    field (TASK-006 flag). Re-derived fresh every cycle from session_open
    (fixed for the session) and current_futures_ltp -- no multi-cycle
    "did it hold" state here, that's TASK-008's hysteresis layer.

    Polarity: gap_and_go -> GO-favorable (continuation), gap_and_fill ->
    NO-GO-favorable (reversion), no_gap -> weak NO-GO baseline (opening
    inside yesterday's equilibrium zone carries no fresh directional
    information). Missing inputs -> (None, reference_band, 0.5, reason).
    """
    name = "gap_classification"
    if (
        session_open is None
        or current_futures_ltp is None
        or prior_value_area is None
        or prior_close is None
    ):
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    _, va_low, va_high = prior_value_area
    gap_direction = session_open - prior_close
    gapped_beyond_va = session_open > va_high or session_open < va_low

    if not gapped_beyond_va:
        raw_value = 0.0
        sub_score = 0.45  # weak NO-GO baseline, no fresh information
    else:
        raw_value = session_open - va_high if session_open > va_high else session_open - va_low
        move_since_open = current_futures_ltp - session_open
        continuing = (move_since_open > 0 and gap_direction > 0) or (
            move_since_open < 0 and gap_direction < 0
        )
        magnitude_pct = _percentile_rank(abs(raw_value), trailing_gap_magnitude_history)
        sub_score = 0.5 + 0.5 * magnitude_pct if continuing else 0.5 - 0.5 * magnitude_pct

    reason = template_reason(name, raw_value, reference_band, sub_score)
    return (raw_value, reference_band, sub_score, reason)


def futures_basis_drift(
    basis_history: list[float],
    price_history: list[float],
    trailing_basis_magnitude_history: list[float],
    reference_band: tuple[float, float],
) -> SignalResult:
    """Resolves OPEN_DECISIONS #4's session-drift scoping. Reuses the
    confirm/diverge shape already established by cvd_direction_and_divergence
    (TASK-006), pcr_level_and_roc (TASK-005), and gex_regime (TASK-004)
    rather than inventing new basis-specific semantics -- the spec gives no
    independent framing for this v2-deferred item pulled forward by
    OPEN_DECISIONS #4.

    Widening basis while price also trends -> confirming (GO-favorable);
    basis moving opposite the price trend -> diverging (NO-GO-favorable).
    Human-approved 2026-07-03 with the "could be pure cost-of-carry noise"
    caveat flagged for a later outcome_labels check.
    """
    name = "futures_basis_drift"
    if len(basis_history) < 2 or len(price_history) < 2:
        reason = template_reason(name, None, reference_band, 0.5)
        return (None, reference_band, 0.5, reason)

    basis_trend = basis_history[-1] - basis_history[0]
    price_trend = price_history[-1] - price_history[0]
    flat_threshold = reference_band[0]

    if abs(price_trend) < flat_threshold:
        sub_score = 0.5
    else:
        confirming = (price_trend > 0 and basis_trend > 0) or (price_trend < 0 and basis_trend < 0)
        magnitude_pct = _percentile_rank(abs(basis_trend), trailing_basis_magnitude_history)
        sub_score = 0.5 + 0.5 * magnitude_pct if confirming else 0.5 - 0.5 * magnitude_pct

    reason = template_reason(
        name, basis_trend, reference_band, sub_score, context={"price_trend": price_trend}
    )
    return (basis_trend, reference_band, sub_score, reason)
