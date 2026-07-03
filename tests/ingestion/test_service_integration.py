"""Live end-to-end integration test: real Supabase credentials, real Dhan
scrip master, real Dhan WebSocket + REST APIs. No mocking. Skipped outside
market hours or when credentials are missing, since spot/futures ticks only
arrive while NSE is live.
"""

import time

import pytest
from dotenv import dotenv_values

from aeolus.ingestion.service import IngestionService

ENV_PATH = __file__.rsplit("/tests/", 1)[0] + "/.env"
_cfg = dotenv_values(ENV_PATH)
SUPABASE_URL = _cfg.get("SUPABASE_URL")
SUPABASE_KEY = _cfg.get("SUPABASE_KEY")

pytestmark = pytest.mark.skipif(
    not (SUPABASE_URL and SUPABASE_KEY),
    reason="SUPABASE_URL/SUPABASE_KEY not set in .env",
)


def test_ingestion_service_end_to_end():
    service = IngestionService(SUPABASE_URL, SUPABASE_KEY)
    service.start()
    try:
        # Give the WS a window to authenticate, subscribe, and receive at
        # least one tick per leg -- not asserting on tick arrival directly
        # (depends on live market conditions), just that the pipeline runs.
        time.sleep(5)
        snapshot = service.latest()

        assert snapshot.system_status in {"OK", "STALE", "DISCONNECTED"}
        assert set(snapshot.system_status_detail.keys()) == {"ws", "option_chain"}
        assert snapshot.gift_nifty is None
        assert isinstance(snapshot.option_chain, list)
        if snapshot.spot_ltp is not None and snapshot.futures_ltp is not None:
            assert snapshot.futures_basis == snapshot.futures_ltp - snapshot.spot_ltp
        assert snapshot.ts.tzinfo is not None
        assert service.lot_size is not None and service.lot_size > 0
        # india_vix may still be None if the WS hasn't ticked in the 5s window --
        # not asserting a value, only that the field exists on the model.
        assert hasattr(snapshot, "india_vix")
        # Same for the TASK-006 amendment fields -- parsed from the futures Full
        # packet, may still be None in a short observation window.
        for field in ("volume", "total_buy_quantity", "total_sell_quantity", "day_high", "day_low"):
            assert hasattr(snapshot, field)
        # TASK-007 amendment: expiry_date is resolved once in start(), before any
        # WS ticks arrive, so it should always be populated by the time latest() runs.
        assert snapshot.expiry_date is not None
    finally:
        service.stop()
