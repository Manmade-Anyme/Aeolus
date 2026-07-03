from datetime import datetime, timedelta, timezone

from aeolus.ingestion.staleness import StalenessTracker, aggregate_status

T0 = datetime(2026, 7, 3, 9, 0, 0, tzinfo=timezone.utc)


def test_untouched_path_is_disconnected():
    tracker = StalenessTracker()
    detail = tracker.detail(T0)
    assert detail == {"ws": "DISCONNECTED", "option_chain": "DISCONNECTED"}
    assert tracker.aggregate(T0) == "DISCONNECTED"


def test_freshly_touched_path_is_ok():
    tracker = StalenessTracker()
    tracker.touch("ws", T0)
    tracker.touch("option_chain", T0)
    assert tracker.aggregate(T0) == "OK"


def test_elapsed_time_not_wall_clock_drives_staleness():
    tracker = StalenessTracker()
    tracker.touch("ws", T0)
    tracker.touch("option_chain", T0)

    just_under_stale = T0 + timedelta(seconds=14)
    assert tracker.detail(just_under_stale)["ws"] == "OK"

    just_over_stale = T0 + timedelta(seconds=16)
    assert tracker.detail(just_over_stale)["ws"] == "STALE"

    disconnected_at = T0 + timedelta(seconds=61)
    assert tracker.detail(disconnected_at)["ws"] == "DISCONNECTED"


def test_worst_of_aggregation():
    detail = {"ws": "OK", "option_chain": "STALE"}
    assert aggregate_status(detail) == "STALE"

    detail = {"ws": "DISCONNECTED", "option_chain": "OK"}
    assert aggregate_status(detail) == "DISCONNECTED"

    detail = {"ws": "OK", "option_chain": "OK"}
    assert aggregate_status(detail) == "OK"


def test_re_touch_resets_staleness():
    tracker = StalenessTracker()
    tracker.touch("ws", T0)
    stale_time = T0 + timedelta(seconds=20)
    assert tracker.detail(stale_time)["ws"] == "STALE"

    tracker.touch("ws", stale_time)
    assert tracker.detail(stale_time)["ws"] == "OK"
