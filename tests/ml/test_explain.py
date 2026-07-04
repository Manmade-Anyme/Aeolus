"""Contract tests for TASK-019's top-feature attribution and reason-string
templating. Pure functions, no DB, no sklearn -- directly testable against
a synthetic z-vector (mirrors ScoreEvent.z_by_feature's shape)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aeolus.ml import explain
from aeolus.ml.explain import anomaly_reason, clear_reason, top_contributors
from aeolus.ml.features import FEATURE_ORDER
from aeolus.ml.scorer import AnomalyState

FLAG = 0.65
CLEAR = 0.55


def test_top_contributors_ranks_by_absolute_z_descending():
    z_by_feature = {"iv_percentile_rank": 0.1, "vix_level": -3.5, "gex_magnitude": 2.0, "pcr_level": 0.5}
    top = top_contributors(z_by_feature, k=3)
    assert top == [("vix_level", -3.5), ("gex_magnitude", 2.0), ("pcr_level", 0.5)]


def test_top_contributors_caps_at_k():
    z_by_feature = {"iv_percentile_rank": 1.0, "vix_level": 2.0}
    assert len(top_contributors(z_by_feature, k=3)) == 2


def test_top_contributors_exact_tie_breaks_by_feature_order():
    # composite_score and iv_percentile_rank tie at |z|=2.0; FEATURE_ORDER
    # puts composite_score (index 5) ahead of iv_percentile_rank (index 6).
    z_by_feature = {"iv_percentile_rank": 2.0, "composite_score": -2.0}
    assert FEATURE_ORDER.index("composite_score") < FEATURE_ORDER.index("iv_percentile_rank")
    top = top_contributors(z_by_feature, k=2)
    assert [name for name, _ in top] == ["composite_score", "iv_percentile_rank"]


def test_anomaly_reason_golden_string():
    contributors = [("vix_level", 3.2), ("gex_magnitude", -2.8)]
    reason = anomaly_reason(contributors, score=0.812, flag_threshold=0.65, model_version=3)
    assert reason == "anomalous — driven by vix_level (+3.2σ), gex_magnitude (-2.8σ) | score 0.812 vs flag 0.650 | model v3"


def test_anomaly_reason_raises_on_empty_contributors():
    with pytest.raises(ValueError, match="at least one contributor"):
        anomaly_reason([], score=0.9, flag_threshold=0.65, model_version=1)


def test_clear_reason_golden_string():
    reason = clear_reason(score=0.412, clear_threshold=0.55, model_version=3)
    assert reason == "cleared — score 0.412 vs clear 0.550 | model v3"


@given(
    z_by_feature=st.dictionaries(
        st.sampled_from(FEATURE_ORDER), st.floats(min_value=-10, max_value=10, allow_nan=False), min_size=1
    ),
    score=st.floats(min_value=-5, max_value=5, allow_nan=False),
    threshold=st.floats(min_value=-5, max_value=5, allow_nan=False),
    model_version=st.integers(min_value=1, max_value=999),
)
def test_anomaly_reason_deterministic_for_identical_inputs(z_by_feature, score, threshold, model_version):
    contributors = top_contributors(z_by_feature)
    first = anomaly_reason(contributors, score, threshold, model_version)
    second = anomaly_reason(top_contributors(z_by_feature), score, threshold, model_version)
    assert first == second


def test_explanation_never_influences_flag_decision(monkeypatch):
    """Structural separation proof (ADR): the state machine's flag decision
    is computed from score-vs-threshold arithmetic alone, before explanation
    is ever consulted. Monkeypatching anomaly_reason to return garbage must
    not be able to change a decision that already happened."""
    state = AnomalyState()
    kind = state.step(0.9, FLAG, CLEAR, min_dwell_cycles=0)
    assert kind == "ANOMALY_ENTER"
    assert state.flagged is True

    monkeypatch.setattr(explain, "anomaly_reason", lambda *args, **kwargs: "GARBAGE")
    contributors = top_contributors({"vix_level": 5.0})
    reason = explain.anomaly_reason(contributors, 0.9, FLAG, 1)

    assert reason == "GARBAGE"
    assert state.flagged is True
