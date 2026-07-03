from aeolus.explain.reason import template_reason


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
