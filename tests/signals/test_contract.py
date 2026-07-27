"""Unit tests for shared signal contract utilities in contract.py."""

from __future__ import annotations

from aeolus.signals.contract import _clamp01, _percentile_rank


def test_percentile_rank_empty_history():
    assert _percentile_rank(100.0, []) == 0.5


def test_percentile_rank_thin_history_fallback():
    # Fewer than min_history items should return 0.5 fallback
    assert _percentile_rank(100.0, [10.0, 20.0], min_history=3) == 0.5


def test_percentile_rank_sufficient_history():
    history = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile_rank(30.0, history, min_history=3) == 0.6  # 3 of 5 <= 30.0
    assert _percentile_rank(55.0, history, min_history=3) == 1.0  # 5 of 5 <= 55.0
    assert _percentile_rank(5.0, history, min_history=3) == 0.0   # 0 of 5 <= 5.0


def test_clamp01():
    assert _clamp01(-0.5) == 0.0
    assert _clamp01(1.5) == 1.0
    assert _clamp01(0.65) == 0.65
