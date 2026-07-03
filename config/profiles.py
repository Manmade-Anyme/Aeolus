"""Two complete EngineConfig instances (TASK-008 ADR, ARES config_profiles.py
pattern) -- NON_EXPIRY_CONFIG and EXPIRY_CONFIG, each a full instance of the
same schema. No partial overrides, no merging between the two.

Every number below is a JUDGMENT-CALIBRATED PLACEHOLDER, not backtested --
no outcome_labels data exists yet (OPEN_DECISIONS #3: live-forward only).
Revisit once TASK-012 accumulates enough history to check these against
real forward outcomes.
"""

from __future__ import annotations

from .tuning import CategoryWeights, EngineConfig, StateThresholds

_NON_EXPIRY_REFERENCE_BANDS: dict[str, tuple[float, float]] = {
    "iv_percentile_rank": (10.0, 25.0),
    "iv_rv_spread": (0.0, 2.0),
    "vix_level_and_roc": (11.0, 20.0),
    "expected_move_consumed_ratio": (0.3, 1.0),
    "gex_regime": (0.0, 5_000_000_000.0),
    "spot_distance_from_flip": (0.0, 2.0),
    "pcr_level_and_roc": (0.0, 0.3),
    "oi_buildup_classification": (0.3, 0.7),
    "oi_wall_proximity_and_strength": (0.0, 2.0),
    "max_pain_drift": (0.0, 100.0),
    "cvd_direction_and_divergence": (20.0, 500.0),
    "delta_imbalance_and_absorption": (0.0, 0.3),
    "volume_participation_range": (0.0, 100.0),
    "prior_day_profile_shape": (1.0, 2.0),
    "gap_classification": (0.0, 100.0),
    "futures_basis_drift": (20.0, 200.0),
}

# Expiry day: IV-percentile band recalibrated lower (IV structurally bleeds
# out through expiry, Spec §8) -- descriptive only for iv_percentile_rank
# today (its sub_score comes from a trailing-history percentile, not this
# band), kept distinct anyway so the config faithfully reflects the spec's
# stated intent for whenever a future revision does read it.
_EXPIRY_REFERENCE_BANDS: dict[str, tuple[float, float]] = {
    **_NON_EXPIRY_REFERENCE_BANDS,
    "iv_percentile_rank": (8.0, 18.0),
}

NON_EXPIRY_CONFIG = EngineConfig(
    category_weights=CategoryWeights(
        volatility=0.25, gamma=0.2, oi_structure=0.2, order_flow=0.2, context=0.15
    ),
    reference_bands=_NON_EXPIRY_REFERENCE_BANDS,
    thresholds=StateThresholds(no_go_prepare=0.40, prepare_go=0.60),
    confirmation_cycles=3,
    min_total_oi=1000,
    volume_participation_pct=0.10,
    min_range_width=20.0,
    order_flow_extreme_threshold=0.10,
    bucket_size=5.0,
    area_pct=0.70,
    expansion_threshold=1.3,
    context_extreme_threshold=0.8,
)

# Gamma/OI walls weighted higher (pinning intensifies into expiry close);
# overall GO bar raised (theta severity means a real move may still not be
# worth buying premium for) -- Spec §8.
EXPIRY_CONFIG = EngineConfig(
    category_weights=CategoryWeights(
        volatility=0.15, gamma=0.30, oi_structure=0.30, order_flow=0.15, context=0.10
    ),
    reference_bands=_EXPIRY_REFERENCE_BANDS,
    thresholds=StateThresholds(no_go_prepare=0.45, prepare_go=0.65),
    confirmation_cycles=3,
    min_total_oi=1000,
    volume_participation_pct=0.10,
    min_range_width=20.0,
    order_flow_extreme_threshold=0.10,
    bucket_size=5.0,
    area_pct=0.70,
    expansion_threshold=1.3,
    context_extreme_threshold=0.8,
)
