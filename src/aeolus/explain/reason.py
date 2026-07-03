"""Reason-string templating (TASK-010 ADR). Every category module
(TASK-003..007) calls template_reason; the composite level (TASK-008's
engine.py) calls explain_transition for state-transition messages.

Deterministic, pinned 2-decimal float formatting. Never LLM-narrated, never
free-text (constraint #3).
"""

from __future__ import annotations


def template_reason(
    signal_name: str,
    raw_value: float | None,
    reference_band: tuple[float, float],
    sub_score: float,
    context: dict[str, float] | None = None,
) -> str:
    """Deterministic reason string from (raw_value, reference_band, sub_score).

    raw_value is None -> explicit "{signal_name}: no data", never fabricated.
    """
    if raw_value is None:
        return f"{signal_name}: no data"

    low, high = reference_band
    reason = f"{signal_name}: {raw_value:.2f} (band {low:.2f}-{high:.2f}, score {sub_score:.2f})"
    if context:
        extras = ", ".join(f"{key}={val:.2f}" for key, val in sorted(context.items()))
        reason = f"{reason} [{extras}]"
    return reason


def explain_transition(
    from_state: str,
    to_state: str,
    trigger_categories: list[str],
    composite_score: float,
    confirmation_cycles: int,
) -> str:
    """Deterministic composite-level transition explanation (Spec §10: must
    cite the category/categories whose sub-score movement actually drove the
    flip). trigger_categories is caller-selected (TASK-008's engine.py picks
    these via its own threshold) -- this function only templates the string,
    it never re-derives which categories qualify.
    """
    if trigger_categories:
        cited = ", ".join(sorted(trigger_categories))
    else:
        cited = "broad-based shift across categories"
    return (
        f"{from_state}->{to_state} driven by {cited}; "
        f"composite={composite_score:.2f}, confirmed after {confirmation_cycles} cycles"
    )
