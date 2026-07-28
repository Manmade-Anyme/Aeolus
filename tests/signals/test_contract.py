"""Unit tests for shared signal contract utilities in contract.py."""

from __future__ import annotations

from aeolus.signals.contract import (
    MIN_PERCENTILE_HISTORY,
    _clamp01,
    _percentile_rank,
)


def test_percentile_rank_empty_history():
    assert _percentile_rank(100.0, []) == 0.5


def test_percentile_rank_thin_history_fallback():
    # Fewer than min_history items returns 0.5 neutral fallback
    assert _percentile_rank(100.0, [10.0, 20.0], min_history=3) == 0.5
    assert _percentile_rank(100.0, [], min_history=1) == 0.5


def test_percentile_rank_sufficient_history():
    history = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile_rank(30.0, history, min_history=3) == 0.6  # 3 of 5 <= 30.0
    assert _percentile_rank(55.0, history, min_history=3) == 1.0  # 5 of 5 <= 55.0
    assert _percentile_rank(5.0, history, min_history=3) == 0.0   # 0 of 5 <= 5.0


def test_percentile_rank_default_guard_blocks_degenerate_samples():
    """The regression this change exists for.

    A 1-element history can only ever rank 0.0 or 1.0, which downstream reads
    as a maximally confident extreme. Under the default guard it must come back
    neutral instead -- from both directions, so neither GO nor NO-GO gets
    fabricated out of a sample too small to support either.
    """
    assert _percentile_rank(100.0, [50.0]) == 0.5   # would saturate to 1.0 unguarded
    assert _percentile_rank(10.0, [50.0]) == 0.5    # would saturate to 0.0 unguarded

    # Guard releases exactly at MIN_PERCENTILE_HISTORY, not before.
    just_under = [1.0] * (MIN_PERCENTILE_HISTORY - 1)
    assert _percentile_rank(100.0, just_under) == 0.5
    assert _percentile_rank(100.0, [*just_under, 1.0]) == 1.0


def test_percentile_rank_min_history_zero_does_not_divide_by_zero():
    assert _percentile_rank(100.0, [], min_history=0) == 0.5


def test_clamp01():
    assert _clamp01(-0.5) == 0.0
    assert _clamp01(1.5) == 1.0
    assert _clamp01(0.65) == 0.65
