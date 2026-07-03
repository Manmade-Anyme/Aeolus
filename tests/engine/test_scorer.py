import pytest

from aeolus.engine.scorer import (
    apply_hysteresis,
    category_score,
    composite_score,
    safe_call,
    state_for_score,
)
from config.tuning import CategoryWeights, StateThresholds

THRESHOLDS = StateThresholds(no_go_prepare=0.4, prepare_go=0.6)
WEIGHTS = CategoryWeights(volatility=0.2, gamma=0.2, oi_structure=0.2, order_flow=0.2, context=0.2)


# --- category_score / composite_score (aggregation, ADR Decision §2) ---


def test_category_score_is_equal_weighted_average():
    assert category_score([0.2, 0.4, 0.6, 0.8]) == pytest.approx(0.5)


def test_category_score_all_neutral_is_neutral():
    assert category_score([0.5, 0.5, 0.5]) == pytest.approx(0.5)


def test_composite_score_weighted_sum_of_categories():
    scores = {"volatility": 1.0, "gamma": 0.0, "oi_structure": 0.5, "order_flow": 0.5, "context": 0.5}
    # equal weights 0.2 each -> (1.0+0.0+0.5+0.5+0.5)*0.2 = 0.5
    assert composite_score(scores, WEIGHTS) == pytest.approx(0.5)


def test_partial_composite_missing_category_contributes_as_neutral():
    """A category with every sub-signal degraded averages to 0.5 and still
    contributes at its configured weight -- never excluded, never renormalized."""
    all_neutral_except_one = {
        "volatility": 0.5,
        "gamma": 0.5,
        "oi_structure": 0.5,
        "order_flow": 0.5,
        "context": 1.0,
    }
    result = composite_score(all_neutral_except_one, WEIGHTS)
    # 4 categories at 0.5 (weight 0.2 each) + 1 at 1.0 (weight 0.2) = 0.4 + 0.2 = 0.6
    assert result == pytest.approx(0.6)


# --- safe_call (ADR Decision §3) ---


def _raises(*_args, **_kwargs):
    raise KeyError("boom")


def _ok(raw, band, score, reason):
    return (raw, band, score, reason)


def test_safe_call_passes_through_on_success():
    result = safe_call("thing", _ok, 1.0, (0.0, 1.0), 0.7, "thing: ok")
    assert result == (1.0, (0.0, 1.0), 0.7, "thing: ok")


def test_safe_call_catches_exception_and_degrades_to_neutral():
    band = (0.0, 1.0)
    raw_value, reference_band, sub_score, reason = safe_call(
        "thing", _raises, "some", "args", reference_band=band
    )
    assert raw_value is None
    assert reference_band == band
    assert sub_score == 0.5
    assert "thing: error (KeyError)" == reason


def test_safe_call_never_propagates_the_exception():
    # if this raised, the test itself would fail with KeyError instead of passing
    safe_call("thing", _raises, reference_band=(0.0, 1.0))


# --- state_for_score ---


def test_state_for_score_boundaries():
    assert state_for_score(0.0, THRESHOLDS) == "NO_GO"
    assert state_for_score(0.39, THRESHOLDS) == "NO_GO"
    assert state_for_score(0.4, THRESHOLDS) == "PREPARE"
    assert state_for_score(0.5, THRESHOLDS) == "PREPARE"
    assert state_for_score(0.6, THRESHOLDS) == "GO"
    assert state_for_score(1.0, THRESHOLDS) == "GO"


# --- apply_hysteresis (ADR Decision §5, directive's named edge case) ---


def test_hysteresis_confirms_after_n_consecutive_cycles():
    pending_state, pending_streak, confirmed_state = "NO_GO", 0, "NO_GO"
    flipped = False
    for _ in range(3):
        pending_state, pending_streak, confirmed_state, flipped = apply_hysteresis(
            "GO", pending_state, pending_streak, confirmed_state, confirmation_cycles=3
        )
    assert confirmed_state == "GO"
    assert flipped is True


def test_hysteresis_oscillating_at_threshold_never_flips():
    """A composite bouncing across a threshold every cycle keeps resetting the
    streak back to 1 -- it can never accumulate confirmation_cycles consecutive
    agreement, so market_state never flips (directive's named edge case)."""
    pending_state, pending_streak, confirmed_state = "NO_GO", 0, "NO_GO"
    proposals = ["PREPARE", "NO_GO"] * 10  # alternates every cycle, never 3 in a row
    for proposal in proposals:
        pending_state, pending_streak, confirmed_state, flipped = apply_hysteresis(
            proposal, pending_state, pending_streak, confirmed_state, confirmation_cycles=3
        )
        assert flipped is False
    assert confirmed_state == "NO_GO"


def test_hysteresis_partial_streak_does_not_flip():
    pending_state, pending_streak, confirmed_state = "NO_GO", 0, "NO_GO"
    for _ in range(2):  # one short of confirmation_cycles=3
        pending_state, pending_streak, confirmed_state, flipped = apply_hysteresis(
            "GO", pending_state, pending_streak, confirmed_state, confirmation_cycles=3
        )
    assert confirmed_state == "NO_GO"
    assert flipped is False


def test_hysteresis_signal_snapshots_always_uses_confirmed_not_pending():
    """market_state_to_persist is always state.confirmed_state -- the flickering
    proposed state is never what gets written to signal_snapshots."""
    pending_state, pending_streak, confirmed_state = "NO_GO", 0, "NO_GO"
    pending_state, pending_streak, confirmed_state, flipped = apply_hysteresis(
        "GO", pending_state, pending_streak, confirmed_state, confirmation_cycles=3
    )
    assert flipped is False
    assert confirmed_state == "NO_GO"  # not yet confirmed, even though proposed=GO this cycle
