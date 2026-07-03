"""Archetype scoring (TASK-009 ADR Decision §3). Pure functions, no I/O.

No existing signal module produces "archetype probabilities" -- every nudge
here is a genuinely new heuristic, not a reuse of an established formula.
Starts from a uniform 1/7 prior and applies independent, bounded
multiplicative nudges per available input, then renormalizes. Every nudge
factor and threshold is a judgment placeholder, not backtested -- flagged
explicitly in the ADR, not presented as settled.
"""

from __future__ import annotations

from aeolus.storage.models import DayArchetype

ARCHETYPES: tuple[DayArchetype, ...] = (
    "clean_trend",
    "grinding_trend",
    "pinned_range",
    "choppy_range",
    "breakout_transition",
    "event_gap",
    "double_distribution",
)


def score_archetypes(
    profile_shape: str | None,
    expanding_vol_pct: float | None,
    dte: int | None,
    gift_nifty_gap: float | None,
    gift_gap_threshold: float,
) -> dict[str, float]:
    """Normalized probability distribution over the 7 DayArchetype values.
    Every input independently optional -- all-None degrades to the uniform
    1/7 prior, never a fabricated lean.
    """
    scores: dict[str, float] = {name: 1.0 for name in ARCHETYPES}

    # Headline driver (Spec §5.1's stated lead line): yesterday's trend exhaustion.
    if profile_shape == "trend":
        scores["clean_trend"] *= 0.5
        scores["grinding_trend"] *= 1.5
        scores["pinned_range"] *= 1.5
    elif profile_shape == "balanced":
        scores["breakout_transition"] *= 1.2
        scores["double_distribution"] *= 1.2

    if expanding_vol_pct is not None:
        if expanding_vol_pct > 0.7:
            scores["clean_trend"] *= 1.3
            scores["choppy_range"] *= 1.3
            scores["event_gap"] *= 1.3
        elif expanding_vol_pct < 0.3:
            scores["grinding_trend"] *= 1.3
            scores["pinned_range"] *= 1.3

    if dte == 0:
        scores["pinned_range"] *= 1.3

    if gift_nifty_gap is not None and abs(gift_nifty_gap) > gift_gap_threshold:
        scores["event_gap"] *= 1.5
        scores["clean_trend"] *= 1.2

    total = sum(scores.values())
    return {name: value / total for name, value in scores.items()}


def primary_and_secondary(distribution: dict[str, float]) -> tuple[str, float, str, float]:
    """(primary_archetype, primary_prob, secondary_archetype, secondary_prob).

    Ties broken by ARCHETYPES' declared order (stable, deterministic).
    """
    ranked = sorted(distribution.items(), key=lambda kv: (-kv[1], ARCHETYPES.index(kv[0])))
    (primary, primary_prob), (secondary, secondary_prob) = ranked[0], ranked[1]
    return primary, primary_prob, secondary, secondary_prob
