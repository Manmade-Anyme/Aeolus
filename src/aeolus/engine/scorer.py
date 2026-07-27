"""Aggregation + hysteresis (TASK-008 ADR Decision §2, §3, §5).

Pure functions only -- no I/O, no state. Engine.run_cycle owns calling these
and updating EngineState with the results.
"""

from __future__ import annotations

from typing import Any, Callable

from config.tuning import CATEGORY_NAMES, StateThresholds
from aeolus.signals.contract import SignalResult
from aeolus.storage.models import MarketState


def safe_call(name: str, fn: Callable[..., SignalResult], *args: Any, **kwargs: Any) -> SignalResult:
    """A crashing sub-signal degrades to a neutral reading for that one
    sub-signal only -- never propagates, never crashes the cycle (ADR §3).
    Distinct from a sub-signal's own graceful None/0.5 path, which handles
    missing *data*; this handles a genuine bug.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, see docstring
        band = kwargs.get("reference_band") or (args[-1] if args else (0.0, 0.0))
        return (None, band, 0.5, f"{name}: error ({exc.__class__.__name__})")


def category_score(sub_scores: list[float]) -> float:
    """Equal-weighted average (ADR Decision §2, human-confirmed) -- a category
    with every sub-signal degraded to 0.5 averages to exactly 0.5, the same
    neutral value every individual sub-signal already uses for missing data.
    """
    return sum(sub_scores) / len(sub_scores)


def composite_score(category_scores: dict[str, float], weights: Any) -> float:
    weight_map = weights.as_dict()
    return sum(weight_map[name] * category_scores[name] for name in CATEGORY_NAMES)


def state_for_score(
    score: float,
    thresholds: StateThresholds,
    volatility_score: float | None = None,
    is_passive_absorption: bool = False,
) -> MarketState:
    if score < thresholds.no_go_prepare:
        return "NO_GO"
    
    # Hard option-buyer gate: If IV is crushing (<0.40) or passive absorption is active,
    # never declare GO (option buying is negative EV).
    if (volatility_score is not None and volatility_score < 0.40) or is_passive_absorption:
        return "PREPARE"

    if score >= thresholds.prepare_go:
        return "GO"
    return "PREPARE"


def apply_hysteresis(
    proposed_state: MarketState,
    pending_state: MarketState,
    pending_streak: int,
    confirmed_state: MarketState,
    confirmation_cycles: int,
) -> tuple[MarketState, int, MarketState, bool]:
    """Returns (new_pending_state, new_pending_streak, new_confirmed_state, flipped).

    N-cycle confirmation only (ADR Decision §5) -- a score oscillating across
    a threshold every cycle keeps resetting pending_streak back to 1, so it
    can never accumulate confirmation_cycles consecutive agreement and never
    flips. confirmed_state is what signal_snapshots.market_state always
    stores; state_transitions is written only when flipped is True.
    """
    if proposed_state == pending_state:
        pending_streak += 1
    else:
        pending_state = proposed_state
        pending_streak = 1

    flipped = False
    if pending_streak >= confirmation_cycles and proposed_state != confirmed_state:
        confirmed_state = proposed_state
        flipped = True

    return pending_state, pending_streak, confirmed_state, flipped
