"""Contract tests for TASK-018's AnomalyState machine and _top_features
helper. No DB, no sklearn -- pure state-machine logic, directly testable
without a live model (the ADR's debounce/hysteresis/min-dwell rules are
entirely score-vs-threshold arithmetic)."""

from aeolus.ml.scorer import AnomalyState, _top_features

FLAG = 0.65
CLEAR = 0.55


def test_normal_scores_produce_no_events():
    state = AnomalyState()
    for score in (0.1, 0.3, 0.5, 0.54):
        assert state.step(score, FLAG, CLEAR, min_dwell_cycles=0) is None
    assert state.flagged is False


def test_spike_produces_single_debounced_anomaly_enter():
    state = AnomalyState()
    assert state.step(0.9, FLAG, CLEAR, min_dwell_cycles=0) == "ANOMALY_ENTER"
    assert state.flagged is True
    # Debounce: staying well above flag threshold never re-fires.
    for score in (0.9, 0.95, 0.7):
        assert state.step(score, FLAG, CLEAR, min_dwell_cycles=0) is None
    assert state.flagged is True


def test_hover_between_thresholds_holds_state_no_flap():
    state = AnomalyState()
    state.step(0.9, FLAG, CLEAR, min_dwell_cycles=0)  # enter
    for score in (0.6, 0.58, 0.64, 0.6):  # strictly between CLEAR and FLAG
        assert state.step(score, FLAG, CLEAR, min_dwell_cycles=0) is None
    assert state.flagged is True


def test_drop_below_clear_produces_single_anomaly_clear():
    state = AnomalyState()
    state.step(0.9, FLAG, CLEAR, min_dwell_cycles=0)  # enter
    assert state.step(0.4, FLAG, CLEAR, min_dwell_cycles=0) == "ANOMALY_CLEAR"
    assert state.flagged is False
    # Debounce applies symmetrically on the way back down.
    assert state.step(0.3, FLAG, CLEAR, min_dwell_cycles=0) is None


def test_boundary_exact_flag_threshold_enters():
    state = AnomalyState()
    assert state.step(FLAG, FLAG, CLEAR, min_dwell_cycles=0) == "ANOMALY_ENTER"


def test_boundary_exact_clear_threshold_does_not_exit():
    state = AnomalyState()
    state.step(0.9, FLAG, CLEAR, min_dwell_cycles=0)
    assert state.step(CLEAR, FLAG, CLEAR, min_dwell_cycles=0) is None
    assert state.flagged is True


def test_min_dwell_default_off_flips_immediately():
    state = AnomalyState()
    assert state.step(0.9, FLAG, CLEAR, min_dwell_cycles=0) == "ANOMALY_ENTER"


def test_min_dwell_blocks_flip_until_state_held_n_cycles():
    state = AnomalyState()
    assert state.step(0.9, FLAG, CLEAR, min_dwell_cycles=2) is None  # dwell 0 -> 1, blocked
    assert state.flagged is False
    assert state.step(0.9, FLAG, CLEAR, min_dwell_cycles=2) is None  # dwell 1 -> 2, blocked
    assert state.flagged is False
    assert state.step(0.9, FLAG, CLEAR, min_dwell_cycles=2) == "ANOMALY_ENTER"  # dwell 2 >= 2, flips
    assert state.flagged is True


def test_min_dwell_blocks_clear_symmetrically():
    state = AnomalyState()
    state.step(0.9, FLAG, CLEAR, min_dwell_cycles=0)  # enter immediately (dwell off for entry)
    assert state.step(0.4, FLAG, CLEAR, min_dwell_cycles=2) is None
    assert state.flagged is True
    assert state.step(0.4, FLAG, CLEAR, min_dwell_cycles=2) is None
    assert state.flagged is True
    assert state.step(0.4, FLAG, CLEAR, min_dwell_cycles=2) == "ANOMALY_CLEAR"
    assert state.flagged is False


def test_top_features_ranks_by_absolute_z_descending():
    z_by_feature = {"a": 0.1, "b": -3.5, "c": 2.0, "d": 0.5}
    top = _top_features(z_by_feature, count=2)
    assert top == [{"name": "b", "z": -3.5}, {"name": "c", "z": 2.0}]


def test_top_features_count_capped_at_available_features():
    z_by_feature = {"a": 1.0, "b": 2.0}
    top = _top_features(z_by_feature, count=3)
    assert len(top) == 2
