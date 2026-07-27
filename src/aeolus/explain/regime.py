"""Regime Classification and Option Buyer Suitability Engine.

Synthesizes raw signal readings and category sub-scores into an explicit,
human-readable Market Regime classification, Option Buyer Suitability rating,
and logical explanation string.
"""

from __future__ import annotations

from typing import Any

from config.tuning import StateThresholds


def classify_regime_and_suitability(
    raw_readings: dict[str, dict[str, Any]],
    sub_scores: dict[str, float],
    composite_score: float,
    thresholds: StateThresholds | None = None,
) -> tuple[str, str, str, str]:
    """Returns (regime_name, suitability_status, suitability_emoji, detailed_explanation).

    Deterministically derived from actual calculated signal values and active profile thresholds.
    """
    no_go_cutoff = thresholds.no_go_prepare if thresholds else 0.40
    go_cutoff = thresholds.prepare_go if thresholds else 0.65

    vol_score = sub_scores.get("volatility", 0.5)

    of_readings = raw_readings.get("order_flow", {})
    cvd_entry = of_readings.get("cvd_direction_and_divergence", {})
    delta_entry = of_readings.get("delta_imbalance_and_absorption", {})
    cvd_score = cvd_entry.get("sub_score", 0.5) if isinstance(cvd_entry, dict) else 0.5
    delta_score = delta_entry.get("sub_score", 0.5) if isinstance(delta_entry, dict) else 0.5

    vol_readings = raw_readings.get("volatility", {})
    iv_rv_entry = vol_readings.get("iv_rv_spread", {})
    iv_rv_score = iv_rv_entry.get("sub_score", 0.5) if isinstance(iv_rv_entry, dict) else 0.5

    is_passive_absorption = cvd_score < 0.40 or delta_score < 0.40
    is_iv_crush = vol_score < 0.40 or iv_rv_score < 0.35

    # 1. Passive Absorption / Short Accumulation
    if is_passive_absorption:
        regime_name = "PASSIVE ABSORPTION / SHORT ACCUMULATION"
        suitability_status = "UNSUITABLE (Passive Limit Absorption Active)"
        suitability_emoji = "🔴"
        explanation = (
            "Price is attempting progress while Cumulative Delta or order book balance shows heavy divergence "
            "(passive limit absorption). Institutional limit orders are absorbing market orders at higher levels; "
            "Call/Put option premiums are decaying due to IV crush & theta bleed."
        )
        return regime_name, suitability_status, suitability_emoji, explanation

    # 2. IV Crush / Theta Bleed
    if is_iv_crush:
        regime_name = "IV CRUSH / THETA BLEED"
        suitability_status = "UNSUITABLE (Implied Volatility Crushing)"
        suitability_emoji = "🔴"
        explanation = (
            "Implied volatility is dropping faster than price movement can expand option premiums. "
            "Theta decay and IV crush make option buying negative expected value."
        )
        return regime_name, suitability_status, suitability_emoji, explanation

    # 3. Pinned Range / Low Volatility
    if composite_score < no_go_cutoff:
        regime_name = "RANGE COMPRESSION / GAMMA PINNING"
        suitability_status = "UNSUITABLE (Range Bound / Pinned)"
        suitability_emoji = "🔴"
        explanation = (
            "Market is compressed within established bounds near major gamma/OI walls. "
            "Directional momentum is absent; option buyers should sit out."
        )
        return regime_name, suitability_status, suitability_emoji, explanation

    # 4. Directional Expansion (GO)
    if composite_score >= go_cutoff and vol_score >= 0.45:
        regime_name = "DIRECTIONAL EXPANSION / VOLATILITY SUPPORT"
        suitability_status = "HIGHLY SUITABLE (Directional Momentum & Vol Expansion)"
        suitability_emoji = "🟢"
        explanation = (
            "Strong directional price momentum supported by expanding implied volatility and aligned order flow. "
            "Option premiums are expanding efficiently with index movement."
        )
        return regime_name, suitability_status, suitability_emoji, explanation

    # 5. Pre-Breakout Accumulation (PREPARE)
    regime_name = "PRE-BREAKOUT ACCUMULATION / MONITORING"
    suitability_status = "STANDBY (Awaiting Confirmed Breakout)"
    suitability_emoji = "🟡"
    explanation = (
        "Market structure is building near key reference levels. Stand by for a confirmed breakout "
        "and volatility expansion before taking option buying positions."
    )
    return regime_name, suitability_status, suitability_emoji, explanation
