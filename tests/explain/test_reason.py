from hypothesis import given
from hypothesis import strategies as st

from aeolus.explain.reason import explain_transition, template_reason


def test_deterministic_same_inputs_byte_identical():
    a = template_reason("iv_percentile_rank", 42.567, (20.0, 80.0), 0.731)
    b = template_reason("iv_percentile_rank", 42.567, (20.0, 80.0), 0.731)
    assert a == b
    assert a == "iv_percentile_rank: 42.57 (band 20.00-80.00, score 0.73)"


def test_none_raw_value_explicit_no_data_string():
    reason = template_reason("vix_level_and_roc", None, (10.0, 30.0), 0.5)
    assert reason == "vix_level_and_roc: no data"


def test_context_appended_without_changing_score_inputs():
    reason = template_reason(
        "iv_rv_spread", 1.5, (-5.0, 5.0), 0.6, context={"iv_minus_rv_spread": 2.25}
    )
    assert reason == "iv_rv_spread: 1.50 (band -5.00-5.00, score 0.60) [iv_minus_rv_spread=2.25]"


@given(
    signal_name=st.text(min_size=1, max_size=20),
    raw_value=st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False)),
    reference_band=st.tuples(
        st.floats(allow_nan=False, allow_infinity=False),
        st.floats(allow_nan=False, allow_infinity=False),
    ),
    sub_score=st.floats(allow_nan=False, allow_infinity=False),
)
def test_template_reason_deterministic_property(signal_name, raw_value, reference_band, sub_score):
    a = template_reason(signal_name, raw_value, reference_band, sub_score)
    b = template_reason(signal_name, raw_value, reference_band, sub_score)
    assert a == b
    if raw_value is None:
        assert a == f"{signal_name}: no data"


def test_explain_transition_cites_trigger_categories():
    reason = explain_transition("NO_GO", "PREPARE", ["gamma", "volatility"], 0.42, 3)
    assert reason == "NO_GO->PREPARE driven by gamma, volatility; composite=0.42, confirmed after 3 cycles"


def test_explain_transition_sorts_categories_for_determinism():
    reason = explain_transition("PREPARE", "GO", ["order_flow", "context"], 0.71, 2)
    assert "context, order_flow" in reason


def test_explain_transition_empty_trigger_categories_broad_based():
    reason = explain_transition("GO", "PREPARE", [], 0.5, 3)
    assert reason == "GO->PREPARE driven by broad-based shift across categories; composite=0.50, confirmed after 3 cycles"


@given(
    from_state=st.sampled_from(["NO_GO", "PREPARE", "GO"]),
    to_state=st.sampled_from(["NO_GO", "PREPARE", "GO"]),
    trigger_categories=st.lists(
        st.sampled_from(["volatility", "gamma", "oi_structure", "order_flow", "context"]),
        max_size=5,
        unique=True,
    ),
    composite_score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    confirmation_cycles=st.integers(min_value=1, max_value=20),
)
def test_explain_transition_deterministic_property(
    from_state, to_state, trigger_categories, composite_score, confirmation_cycles
):
    a = explain_transition(from_state, to_state, trigger_categories, composite_score, confirmation_cycles)
    b = explain_transition(from_state, to_state, trigger_categories, composite_score, confirmation_cycles)
    assert a == b
