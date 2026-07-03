"""Interim reason-string stub (TASK-003 ADR), signature-compatible with
TASK-010's eventual real templater. Nothing in signal modules changes when
TASK-010 lands.

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
