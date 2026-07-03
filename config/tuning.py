"""EngineConfig schema (TASK-008 ADR) -- pydantic-settings, ARES's exact
pattern: BaseSettings fields with no defaults on judgment-calibrated knobs,
so a missing value is a startup ValidationError, never a silent zero.

Category names and sub-signal names below are fixed by which categories/
sub-signals TASK-003..007 actually shipped -- not meant to be extended
without a matching signal module.
"""

from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings

CATEGORY_NAMES = ("volatility", "gamma", "oi_structure", "order_flow", "context")

SUB_SIGNAL_NAMES = (
    "iv_percentile_rank",
    "iv_rv_spread",
    "vix_level_and_roc",
    "expected_move_consumed_ratio",
    "gex_regime",
    "spot_distance_from_flip",
    "pcr_level_and_roc",
    "oi_buildup_classification",
    "oi_wall_proximity_and_strength",
    "max_pain_drift",
    "cvd_direction_and_divergence",
    "delta_imbalance_and_absorption",
    "volume_participation_range",
    "prior_day_profile_shape",
    "gap_classification",
    "futures_basis_drift",
)


class CategoryWeights(BaseSettings):
    volatility: float
    gamma: float
    oi_structure: float
    order_flow: float
    context: float

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "CategoryWeights":
        total = self.volatility + self.gamma + self.oi_structure + self.order_flow + self.context
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"category weights must sum to 1.0, got {total}")
        return self

    def as_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in CATEGORY_NAMES}


class StateThresholds(BaseSettings):
    no_go_prepare: float  # composite below this -> NO_GO
    prepare_go: float  # composite at/above this -> GO; between the two -> PREPARE

    @model_validator(mode="after")
    def _ordered(self) -> "StateThresholds":
        if not (0.0 <= self.no_go_prepare < self.prepare_go <= 1.0):
            raise ValueError(
                "thresholds must satisfy 0.0 <= no_go_prepare < prepare_go <= 1.0, "
                f"got no_go_prepare={self.no_go_prepare}, prepare_go={self.prepare_go}"
            )
        return self


class EngineConfig(BaseSettings):
    category_weights: CategoryWeights
    reference_bands: dict[str, tuple[float, float]]
    thresholds: StateThresholds
    confirmation_cycles: int
    min_total_oi: int
    volume_participation_pct: float
    min_range_width: float
    order_flow_extreme_threshold: float
    bucket_size: float
    area_pct: float
    expansion_threshold: float
    context_extreme_threshold: float

    @model_validator(mode="after")
    def _reference_bands_cover_every_sub_signal(self) -> "EngineConfig":
        missing = set(SUB_SIGNAL_NAMES) - set(self.reference_bands)
        if missing:
            raise ValueError(f"reference_bands missing entries for: {sorted(missing)}")
        return self
