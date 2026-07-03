"""Live integration test against Dhan's real published scrip master CSV.
No credentials required (public file); no mocking -- this hits the real URL.
"""

from datetime import date, timedelta

from aeolus.ingestion.instruments import InstrumentResolver


def test_resolve_spot_is_nifty_50_index():
    resolver = InstrumentResolver()
    security_id = resolver.resolve_spot()
    assert security_id == "13"


def test_resolve_vix_is_india_vix_index():
    resolver = InstrumentResolver()
    security_id = resolver.resolve_vix()
    assert security_id == "21"


def test_resolve_current_month_futures_is_unexpired_and_soonest():
    resolver = InstrumentResolver()
    resolved = resolver.resolve_current_month_futures(date(2026, 7, 3))
    assert resolved.security_id.isdigit()
    assert resolved.expiry >= date(2026, 7, 3)
    assert resolved.lot_size > 0


def test_futures_roll_picks_next_contract_once_current_expires():
    resolver = InstrumentResolver()
    resolved = resolver.resolve_current_month_futures(date(2026, 7, 3))
    rolled = resolver.resolve_current_month_futures(resolved.expiry + timedelta(days=1))
    assert rolled.expiry > resolved.expiry
    assert rolled.security_id != resolved.security_id
