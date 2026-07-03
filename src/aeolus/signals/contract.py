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


def _percentile_rank(value: float, history: list[float]) -> float:
    """Fraction of history <= value, as a 0.0-1.0 percentile rank.

    Empty history -> 0.5 (no basis for comparison, matches sub_score's own
    neutral/unknown convention).
    """
    if not history:
        return 0.5
    return sum(1 for h in history if h <= value) / len(history)
