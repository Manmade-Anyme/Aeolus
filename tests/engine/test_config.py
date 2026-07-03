"""EngineConfig validation (TASK-008 ADR Decision §1) -- construction IS the
validation, ARES's fail-fast pattern. No file to be invalid; a bad value is
a pydantic.ValidationError raised at EngineConfig(...) construction time."""

import pytest
from pydantic import ValidationError

from config.profiles import EXPIRY_CONFIG, NON_EXPIRY_CONFIG
from config.tuning import SUB_SIGNAL_NAMES, CategoryWeights, EngineConfig, StateThresholds


def _valid_kwargs(**overrides):
    kwargs = dict(
        category_weights=CategoryWeights(
            volatility=0.2, gamma=0.2, oi_structure=0.2, order_flow=0.2, context=0.2
        ),
        reference_bands={name: (0.0, 1.0) for name in SUB_SIGNAL_NAMES},
        thresholds=StateThresholds(no_go_prepare=0.4, prepare_go=0.6),
        confirmation_cycles=3,
        min_total_oi=1000,
        volume_participation_pct=0.1,
        min_range_width=20.0,
        order_flow_extreme_threshold=0.1,
        bucket_size=5.0,
        area_pct=0.7,
        expansion_threshold=1.3,
        context_extreme_threshold=0.8,
    )
    kwargs.update(overrides)
    return kwargs


def test_valid_config_constructs():
    EngineConfig(**_valid_kwargs())


def test_category_weights_not_summing_to_one_raises():
    with pytest.raises(ValidationError):
        CategoryWeights(volatility=0.5, gamma=0.5, oi_structure=0.5, order_flow=0.5, context=0.5)


def test_thresholds_out_of_order_raises():
    with pytest.raises(ValidationError):
        StateThresholds(no_go_prepare=0.6, prepare_go=0.4)


def test_missing_reference_band_entry_raises():
    incomplete_bands = {name: (0.0, 1.0) for name in SUB_SIGNAL_NAMES if name != "gex_regime"}
    with pytest.raises(ValidationError):
        EngineConfig(**_valid_kwargs(reference_bands=incomplete_bands))


def test_missing_required_field_raises():
    kwargs = _valid_kwargs()
    del kwargs["confirmation_cycles"]
    with pytest.raises(ValidationError):
        EngineConfig(**kwargs)


def test_expiry_and_non_expiry_profiles_are_both_valid_and_distinct():
    assert EXPIRY_CONFIG.category_weights.as_dict() != NON_EXPIRY_CONFIG.category_weights.as_dict()
    assert EXPIRY_CONFIG.thresholds.prepare_go > NON_EXPIRY_CONFIG.thresholds.prepare_go
