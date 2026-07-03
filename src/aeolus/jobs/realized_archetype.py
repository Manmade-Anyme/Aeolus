"""Realized day-archetype classifier (TASK-012 ADR). Pure function, no I/O --
classifies a *completed* session's actual price/IV action into one of the 7
DayArchetype values, for backfilling daily_outlook.realized_archetype.

Build-time correction to the ADR: the ADR proposed reusing
context_signals.prior_day_profile_shape for the whole-day directional read.
That function needs trailing_average_range_history, which is
EngineState-internal in-memory data (engine.py), never persisted to
signal_snapshots -- unavailable to a standalone job reading only from
Supabase. Implemented instead as a self-contained range/close-location
helper needing nothing but this session's own stored futures_ltp readings.
"""

from __future__ import annotations

from aeolus.storage.models import DayArchetype, SignalSnapshot

_IV_EPSILON = 0.01

_BASE_QUADRANT: dict[tuple[str, bool], DayArchetype] = {
    ("trend", True): "clean_trend",
    ("trend", False): "grinding_trend",
    ("balanced", False): "pinned_range",
    ("balanced", True): "choppy_range",
}


def _carry(snapshot: SignalSnapshot, category: str, key: str) -> float | None:
    value = snapshot.raw_readings.get(category, {}).get("_carry", {}).get(key)
    return float(value) if value is not None else None


def _range_close_location_class(prices: list[float]) -> str:
    """'trend' if the last price sits in the outer third of the range this
    price series traced out, 'balanced' otherwise (including a degenerate
    zero-range series). Same close-location-within-range intuition as
    context_signals.prior_day_profile_shape, self-contained rather than
    reusing that function's cross-day-history-dependent signature."""
    high, low, close = max(prices), min(prices), prices[-1]
    price_range = high - low
    if price_range == 0:
        return "balanced"
    close_location = (close - low) / price_range
    return "trend" if close_location <= 1 / 3 or close_location >= 2 / 3 else "balanced"


def _iv_spiked_then_crushed(ivs: list[float]) -> bool:
    peak_index = max(range(len(ivs)), key=lambda i: ivs[i])
    if peak_index == 0 or peak_index == len(ivs) - 1:
        return False
    peak = ivs[peak_index]
    return peak > ivs[0] + _IV_EPSILON and peak > ivs[-1] + _IV_EPSILON


def classify_realized_archetype(snapshots: list[SignalSnapshot]) -> DayArchetype | None:
    """None when there isn't enough data to classify honestly -- never
    fabricates a read, matching this project's None-means-don't-know
    discipline (same as template_reason's raw_value=None handling)."""
    ordered = sorted(snapshots, key=lambda s: s.ts)
    futures_ltps = [v for s in ordered if (v := _carry(s, "order_flow", "futures_ltp")) is not None]
    ivs = [v for s in ordered if (v := _carry(s, "volatility", "current_iv")) is not None]

    if len(futures_ltps) < 2 or len(ivs) < 2:
        return None

    directional = _range_close_location_class(futures_ltps)
    volatility_expanding = ivs[-1] > ivs[0] + _IV_EPSILON
    base = _BASE_QUADRANT[(directional, volatility_expanding)]

    mid = len(ordered) // 2
    first_half = [v for s in ordered[:mid] if (v := _carry(s, "order_flow", "futures_ltp")) is not None]
    second_half = [v for s in ordered[mid:] if (v := _carry(s, "order_flow", "futures_ltp")) is not None]
    if (
        len(first_half) >= 2
        and len(second_half) >= 2
        and _range_close_location_class(first_half) == "balanced"
        and _range_close_location_class(second_half) == "trend"
    ):
        return "breakout_transition"

    first_gap_reading = (
        ordered[0].raw_readings.get("context", {}).get("gap_classification", {}).get("raw_value")
    )
    if first_gap_reading not in (None, 0.0) and _iv_spiked_then_crushed(ivs):
        return "event_gap"

    return base
