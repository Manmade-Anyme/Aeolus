"""Contract tests for TASK-015 ml feature extractor. No internal mocking --
exercises extract_features/standardize against a fixture SignalSnapshot
shaped exactly like real engine._category_raw_readings output
(engine.py:412-424: {sub_signal: {raw_value, reference_band, sub_score,
context?}} per category, plus a "_carry" key engines add for cross-cycle
state -- accessors must ignore it same as any other unknown key)."""

from datetime import date, datetime, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aeolus.ml.features import FEATURE_ORDER, FEATURE_SET_VERSION, Scaler, extract_features, standardize
from aeolus.storage.models import SignalSnapshot


def _raw_readings(
    *,
    vix_roc: float | None = 1.2,
    pcr_level: float | None = 0.85,
    gex_raw: float | None = -15000.0,
) -> dict:
    vix_entry = {"raw_value": 14.5, "reference_band": [10.0, 30.0], "sub_score": 0.6}
    if vix_roc is not None:
        vix_entry["context"] = {"roc": vix_roc}

    pcr_entry = {"raw_value": 0.03, "reference_band": [0.0, 0.1], "sub_score": 0.55}
    if pcr_level is not None:
        pcr_entry["context"] = {"pcr_level": pcr_level}

    return {
        "volatility": {
            "iv_percentile_rank": {"raw_value": 42.5, "reference_band": [20.0, 80.0], "sub_score": 0.7},
            "vix_level_and_roc": vix_entry,
            "expected_move_consumed_ratio": {"raw_value": 0.4, "reference_band": [0.3, 0.9], "sub_score": 0.3},
            "_carry": {"current_iv": 14.5, "spot_ltp": 24500.0, "india_vix": 14.5},
        },
        "gamma": {
            "gex_regime": (
                {"raw_value": gex_raw, "reference_band": [0.0, 1.0], "sub_score": 0.65}
                if gex_raw is not None
                else {"raw_value": None, "reference_band": [0.0, 1.0], "sub_score": 0.5}
            ),
            "spot_distance_from_flip": {"raw_value": 1.8, "reference_band": [0.5, 3.0], "sub_score": 0.4},
        },
        "oi_structure": {
            "pcr_level_and_roc": pcr_entry,
            "_carry": {"max_pain": 24450.0},
        },
        "order_flow": {
            "cvd_direction_and_divergence": {"raw_value": 320.0, "reference_band": [50.0, 500.0], "sub_score": 0.75},
            "_carry": {"cvd_delta": 12.0},
        },
        "context": {
            "profile_shape": "grinding_trend",
            "_carry": {"futures_ltp": 24510.0},
        },
    }


def _snapshot(
    *,
    system_status: str = "OK",
    vix_roc: float | None = 1.2,
    pcr_level: float | None = 0.85,
    gex_raw: float | None = -15000.0,
) -> SignalSnapshot:
    return SignalSnapshot(
        ts=datetime(2026, 7, 3, 9, 20, tzinfo=timezone.utc),
        session_date=date(2026, 7, 3),
        config_type="NON_EXPIRY",
        dte=3,
        raw_readings=_raw_readings(vix_roc=vix_roc, pcr_level=pcr_level, gex_raw=gex_raw),
        sub_scores={
            "volatility": 0.6,
            "gamma": 0.55,
            "oi_structure": 0.5,
            "order_flow": 0.75,
            "context": 0.45,
        },
        composite_score=0.58,
        market_state="PREPARE",
        system_status=system_status,  # type: ignore[arg-type]
        reasons={},
    )


def test_extract_features_pulls_expected_values():
    vector = extract_features(_snapshot())
    assert vector is not None
    assert vector["sub_score_volatility"] == 0.6
    assert vector["sub_score_gamma"] == 0.55
    assert vector["sub_score_oi_structure"] == 0.5
    assert vector["sub_score_order_flow"] == 0.75
    assert vector["sub_score_context"] == 0.45
    assert vector["composite_score"] == 0.58
    assert vector["iv_percentile_rank"] == 42.5
    assert vector["vix_level"] == 14.5
    assert vector["vix_roc"] == 1.2
    assert vector["pcr_level"] == 0.85
    assert vector["pcr_roc"] == 0.03
    assert vector["gex_magnitude"] == 15000.0
    assert vector["spot_distance_from_flip"] == 1.8
    assert vector["expected_move_consumed_ratio"] == 0.4
    assert vector["cvd_divergence"] == 320.0


def test_extract_features_keys_match_feature_order_exactly():
    vector = extract_features(_snapshot())
    assert vector is not None
    assert list(vector.keys()) == list(FEATURE_ORDER)


@pytest.mark.parametrize("bad_status", ["STALE", "DISCONNECTED"])
def test_extract_features_none_for_broken_feed(bad_status):
    assert extract_features(_snapshot(system_status=bad_status)) is None


def test_missing_context_leaf_is_none_others_intact():
    vector = extract_features(_snapshot(vix_roc=None, pcr_level=None))
    assert vector is not None
    assert vector["vix_roc"] is None
    assert vector["pcr_level"] is None
    # Everything else in the same category is unaffected by one missing leaf.
    assert vector["vix_level"] == 14.5
    assert vector["pcr_roc"] == 0.03


def test_missing_raw_value_is_none_others_intact():
    vector = extract_features(_snapshot(gex_raw=None))
    assert vector is not None
    assert vector["gex_magnitude"] is None
    assert vector["spot_distance_from_flip"] == 1.8


def test_extract_features_deterministic_same_snapshot_same_vector():
    snapshot = _snapshot()
    assert extract_features(snapshot) == extract_features(snapshot)


@given(
    vix_roc=st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False, width=32)),
    pcr_level=st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False, width=32)),
    gex_raw=st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False, width=32)),
)
def test_extract_features_deterministic_property(vix_roc, pcr_level, gex_raw):
    snapshot = _snapshot(vix_roc=vix_roc, pcr_level=pcr_level, gex_raw=gex_raw)
    assert extract_features(snapshot) == extract_features(snapshot)


def test_standardize_matches_hand_computed_z():
    raw = {name: float(i) for i, name in enumerate(FEATURE_ORDER)}
    scaler = Scaler(
        mean={name: 1.0 for name in FEATURE_ORDER},
        std={name: 2.0 for name in FEATURE_ORDER},
    )
    z = standardize(raw, scaler)
    expected = [(float(i) - 1.0) / 2.0 for i in range(len(FEATURE_ORDER))]
    assert z == expected


def test_standardize_raises_key_error_on_missing_feature():
    raw = {name: 0.0 for name in FEATURE_ORDER if name != FEATURE_ORDER[0]}
    scaler = Scaler(mean=dict.fromkeys(FEATURE_ORDER, 0.0), std=dict.fromkeys(FEATURE_ORDER, 1.0))
    with pytest.raises(KeyError):
        standardize(raw, scaler)


def test_config_type_not_in_feature_order():
    assert "config_type" not in FEATURE_ORDER


def test_feature_set_version_is_one():
    assert FEATURE_SET_VERSION == 1
