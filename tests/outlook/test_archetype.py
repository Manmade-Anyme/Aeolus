import pytest

from aeolus.outlook.archetype import ARCHETYPES, primary_and_secondary, score_archetypes


def _uniform() -> float:
    return 1.0 / len(ARCHETYPES)


def test_all_none_inputs_produce_the_uniform_prior():
    distribution = score_archetypes(
        profile_shape=None,
        expanding_vol_pct=None,
        dte=None,
        gift_nifty_gap=None,
        gift_gap_threshold=50.0,
    )
    assert set(distribution) == set(ARCHETYPES)
    for prob in distribution.values():
        assert prob == pytest.approx(_uniform())
    assert sum(distribution.values()) == pytest.approx(1.0)


def test_trend_exhaustion_shifts_mass_toward_grinding_and_pinned():
    baseline = score_archetypes(None, None, None, None, 50.0)
    distribution = score_archetypes(
        profile_shape="trend",
        expanding_vol_pct=None,
        dte=None,
        gift_nifty_gap=None,
        gift_gap_threshold=50.0,
    )
    assert distribution["grinding_trend"] > baseline["grinding_trend"]
    assert distribution["pinned_range"] > baseline["pinned_range"]
    assert distribution["clean_trend"] < baseline["clean_trend"]
    assert sum(distribution.values()) == pytest.approx(1.0)


def test_balanced_profile_shifts_mass_toward_breakout_and_double_distribution():
    baseline = score_archetypes(None, None, None, None, 50.0)
    distribution = score_archetypes(
        profile_shape="balanced",
        expanding_vol_pct=None,
        dte=None,
        gift_nifty_gap=None,
        gift_gap_threshold=50.0,
    )
    assert distribution["breakout_transition"] > baseline["breakout_transition"]
    assert distribution["double_distribution"] > baseline["double_distribution"]


def test_high_expanding_vol_favors_expanding_vol_archetypes():
    distribution = score_archetypes(None, 0.9, None, None, 50.0)
    baseline = score_archetypes(None, None, None, None, 50.0)
    assert distribution["clean_trend"] > baseline["clean_trend"]
    assert distribution["choppy_range"] > baseline["choppy_range"]
    assert distribution["event_gap"] > baseline["event_gap"]


def test_low_expanding_vol_favors_contracting_vol_archetypes():
    distribution = score_archetypes(None, 0.1, None, None, 50.0)
    baseline = score_archetypes(None, None, None, None, 50.0)
    assert distribution["grinding_trend"] > baseline["grinding_trend"]
    assert distribution["pinned_range"] > baseline["pinned_range"]


def test_expiry_day_boosts_pinned_range():
    distribution = score_archetypes(None, None, 0, None, 50.0)
    baseline = score_archetypes(None, None, 5, None, 50.0)
    assert distribution["pinned_range"] > baseline["pinned_range"]


def test_large_gift_gap_boosts_event_gap():
    distribution = score_archetypes(None, None, None, 80.0, 50.0)
    baseline = score_archetypes(None, None, None, None, 50.0)
    assert distribution["event_gap"] > baseline["event_gap"]


def test_gift_gap_below_threshold_has_no_effect():
    distribution = score_archetypes(None, None, None, 10.0, 50.0)
    baseline = score_archetypes(None, None, None, None, 50.0)
    assert distribution == pytest.approx(baseline)


def test_primary_and_secondary_ranks_correctly():
    distribution = {
        "clean_trend": 0.05,
        "grinding_trend": 0.30,
        "pinned_range": 0.25,
        "choppy_range": 0.10,
        "breakout_transition": 0.10,
        "event_gap": 0.10,
        "double_distribution": 0.10,
    }
    primary, primary_prob, secondary, secondary_prob = primary_and_secondary(distribution)
    assert primary == "grinding_trend"
    assert primary_prob == pytest.approx(0.30)
    assert secondary == "pinned_range"
    assert secondary_prob == pytest.approx(0.25)


def test_primary_and_secondary_breaks_ties_by_declared_order():
    distribution = {name: 1.0 / len(ARCHETYPES) for name in ARCHETYPES}
    primary, _, secondary, _ = primary_and_secondary(distribution)
    assert primary == ARCHETYPES[0]
    assert secondary == ARCHETYPES[1]
