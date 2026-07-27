"""Unit tests for Regime Classification and Option Buyer Suitability Engine."""

from __future__ import annotations

from aeolus.explain.regime import classify_regime_and_suitability


def test_classify_regime_passive_absorption() -> None:
    raw_readings = {
        "order_flow": {
            "cvd_direction_and_divergence": {"sub_score": 0.20},
            "delta_imbalance_and_absorption": {"sub_score": 0.50},
        },
        "volatility": {"iv_rv_spread": {"sub_score": 0.50}},
    }
    sub_scores = {"volatility": 0.50, "gamma": 0.80, "order_flow": 0.35}
    regime, suitability, emoji, explanation = classify_regime_and_suitability(
        raw_readings, sub_scores, 0.62
    )

    assert regime == "PASSIVE ABSORPTION / SHORT ACCUMULATION"
    assert "UNSUITABLE" in suitability
    assert emoji == "🔴"
    assert "passive limit absorption" in explanation


def test_classify_regime_iv_crush() -> None:
    raw_readings = {
        "order_flow": {
            "cvd_direction_and_divergence": {"sub_score": 0.70},
            "delta_imbalance_and_absorption": {"sub_score": 0.70},
        },
        "volatility": {"iv_rv_spread": {"sub_score": 0.25}},
    }
    sub_scores = {"volatility": 0.35, "gamma": 0.90, "order_flow": 0.70}
    regime, suitability, emoji, explanation = classify_regime_and_suitability(
        raw_readings, sub_scores, 0.63
    )

    assert regime == "IV CRUSH / THETA BLEED"
    assert "UNSUITABLE" in suitability
    assert emoji == "🔴"
    assert "Implied volatility is dropping" in explanation


def test_classify_regime_directional_expansion() -> None:
    raw_readings = {
        "order_flow": {
            "cvd_direction_and_divergence": {"sub_score": 0.85},
            "delta_imbalance_and_absorption": {"sub_score": 0.80},
        },
        "volatility": {"iv_rv_spread": {"sub_score": 0.75}},
    }
    sub_scores = {"volatility": 0.65, "gamma": 0.85, "order_flow": 0.80}
    regime, suitability, emoji, explanation = classify_regime_and_suitability(
        raw_readings, sub_scores, 0.72
    )

    assert regime == "DIRECTIONAL EXPANSION / VOLATILITY SUPPORT"
    assert "HIGHLY SUITABLE" in suitability
    assert emoji == "🟢"
    assert "Strong directional price momentum" in explanation


def test_classify_regime_with_expiry_thresholds() -> None:
    from config.tuning import StateThresholds

    expiry_thresholds = StateThresholds(no_go_prepare=0.45, prepare_go=0.68)
    raw_readings = {
        "order_flow": {
            "cvd_direction_and_divergence": {"sub_score": 0.75},
            "delta_imbalance_and_absorption": {"sub_score": 0.75},
        },
        "volatility": {"iv_rv_spread": {"sub_score": 0.60}},
    }
    sub_scores = {"volatility": 0.60, "gamma": 0.75, "order_flow": 0.75}

    # Composite 0.66 is below 0.68 (expiry prepare_go), should yield PREPARE / STANDBY
    regime, suitability, emoji, explanation = classify_regime_and_suitability(
        raw_readings, sub_scores, 0.66, thresholds=expiry_thresholds
    )
    assert regime == "PRE-BREAKOUT ACCUMULATION / MONITORING"
    assert "STANDBY" in suitability
    assert emoji == "🟡"
