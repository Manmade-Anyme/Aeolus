"""Unit-level parsing tests for LiveFeed._on_message -- no live WS connection,
no network I/O. Constructs synthetic packets matching dhanhq's actual
process_full()/Ticker Data field shapes and asserts what LiveFeed extracts.
"""

from aeolus.ingestion.credentials import DhanCredentials
from aeolus.ingestion.feed_ws import LiveFeed
from aeolus.ingestion.staleness import StalenessTracker

FUTURES_ID = "49081"
SPOT_ID = "13"
VIX_ID = "21"


def _feed() -> LiveFeed:
    return LiveFeed(
        DhanCredentials(client_id="x", access_token="y"),
        spot_security_id=SPOT_ID,
        futures_security_id=FUTURES_ID,
        vix_security_id=VIX_ID,
        staleness=StalenessTracker(),
    )


def _full_data(**overrides: object) -> dict[str, object]:
    packet: dict[str, object] = {
        "type": "Full Data",
        "security_id": FUTURES_ID,
        "LTP": "24550.00",
        "volume": 1_500_000,
        "total_buy_quantity": 800_000,
        "total_sell_quantity": 700_000,
        "open": "24500.00",
        "close": "24480.00",
        "high": "24600.00",
        "low": "24400.00",
        "depth": [],
    }
    packet.update(overrides)
    return packet


def test_full_packet_parses_volume_and_buy_sell_quantity():
    feed = _feed()
    feed._on_message(_full_data())
    assert feed.latest_volume() == 1_500_000
    assert feed.latest_total_buy_quantity() == 800_000
    assert feed.latest_total_sell_quantity() == 700_000


def test_full_packet_parses_day_high_low_from_high_low_fields():
    feed = _feed()
    feed._on_message(_full_data())
    assert feed.latest_day_high() == 24600.0
    assert feed.latest_day_low() == 24400.0


def test_full_packet_wrong_security_id_ignored():
    feed = _feed()
    feed._on_message(_full_data(security_id="99999"))
    assert feed.latest_volume() is None
    assert feed.latest_day_high() is None
