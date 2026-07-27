"""Shared contract for all category signal modules (TASK-003..007).

SignalResult is the standard 4-tuple every sub-signal function returns —
fixed by the TASK-003 ADR, binding on every later category module.
"""

from __future__ import annotations

SignalResult = tuple[float | None, tuple[float, float], float, str]
"""(raw_value, reference_band, sub_score, reason_string).

raw_value: None only when the underlying input is genuinely missing.
reference_band: numeric (low, high) band raw_value is compared against.
sub_score: 0.0-1.0, 1.0 = maximally GO-favorable, 0.0 = maximally NO-GO-favorable,
    0.5 = neutral / insufficient-data fallback.
"""

MIN_LOOKBACK_SESSIONS = 20

# Minimum sample a percentile may be computed from. Deliberately lower than
# MIN_LOOKBACK_SESSIONS because several callers rank against *derived* series
# that are structurally shorter than the session count: vix_level_and_roc's
# roc_history is N-1 diffs, and iv_rv_spread's rising/falling magnitude
# histories split those N-1 diffs across two lists (~N/2 each). Gating those
# at 20 would pin them to 0.5 permanently; 10 is reachable within the engine's
# 60-session seed while still being too large to saturate off noise.
MIN_PERCENTILE_HISTORY = 10


def _percentile_rank(
    value: float,
    history: list[float],
    min_history: int = MIN_PERCENTILE_HISTORY,
) -> float:
    """Fraction of history <= value, as a 0.0-1.0 percentile rank.

    Fewer than min_history samples -> 0.5 (no basis for comparison, matches
    sub_score's own neutral/unknown convention).

    The guard defaults to MIN_PERCENTILE_HISTORY rather than to 1 on purpose.
    A percentile over a 1-2 element history is not "slightly noisy", it is
    degenerate: it can only return 0.0 or 1.0, which downstream reads as a
    maximally confident extreme. That is exactly how gex_regime,
    cvd_direction_and_divergence and delta_imbalance_and_absorption came to
    sit locked at sub_score 1.00 in live sessions. Defaulting the guard on
    (rather than requiring all 14 call sites to opt in) means a new call site
    cannot reintroduce that failure by forgetting to pass it.
    """
    # `not history` is kept explicit so a caller passing min_history=0 cannot
    # reach the ZeroDivisionError below.
    if not history or len(history) < min_history:
        return 0.5
    return sum(1 for h in history if h <= value) / len(history)


def _clamp01(value: float) -> float:
    """Clamp value to the 0.0-1.0 sub_score range."""
    return max(0.0, min(1.0, value))
