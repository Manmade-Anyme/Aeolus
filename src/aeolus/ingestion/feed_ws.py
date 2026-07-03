"""Live WebSocket feed (TASK-002 ADR): spot LTP, futures LTP, market depth.

Wraps dhanhq.MarketFeed (v2 binary wire protocol -- struct-level packet
parsing lives in the SDK, not reimplemented here). Reconnect/backoff is owned
by this module rather than the SDK's built-in loop, which only retries on a
fixed 1s sleep: we drive the feed's connect()/get_instrument_data()/
disconnect() primitives directly from our own supervisor loop to get true
exponential backoff (capped at 30s, reset after a sustained connection).

v2 has no standalone Depth request code (only Ticker/Quote/Full) -- market
depth is obtained by subscribing the futures leg at Full, which bundles
5-level depth with LTP/volume/OI in one packet.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import Any

from dhanhq import DhanContext, MarketFeed

from aeolus.ingestion.credentials import DhanCredentials
from aeolus.ingestion.models import DepthLevel, MarketDepth
from aeolus.ingestion.staleness import StalenessTracker

_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 30.0
_BACKOFF_RESET_AFTER_SECONDS = 60.0


class LiveFeed:
    """Subscribes to NIFTY spot (Ticker) and current-month futures (Full)."""

    def __init__(
        self,
        credentials: DhanCredentials,
        spot_security_id: str,
        futures_security_id: str,
        staleness: StalenessTracker,
    ) -> None:
        self._context = DhanContext(credentials.client_id, credentials.access_token)
        self._instruments = [
            (MarketFeed.IDX, spot_security_id, MarketFeed.Ticker),
            (MarketFeed.NSE_FNO, futures_security_id, MarketFeed.Full),
        ]
        self._spot_security_id = spot_security_id
        self._futures_security_id = futures_security_id
        self._staleness = staleness

        self._lock = threading.Lock()
        self._spot_ltp: float | None = None
        self._futures_ltp: float | None = None
        self._depth: MarketDepth | None = None

        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._supervise, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def latest_spot_ltp(self) -> float | None:
        with self._lock:
            return self._spot_ltp

    def latest_futures_ltp(self) -> float | None:
        with self._lock:
            return self._futures_ltp

    def latest_depth(self) -> MarketDepth | None:
        with self._lock:
            return self._depth

    def _supervise(self) -> None:
        asyncio.run(self._supervise_async())

    async def _supervise_async(self) -> None:
        backoff = _INITIAL_BACKOFF_SECONDS
        while self._running:
            feed = MarketFeed(self._context, self._instruments, version="v2")
            connected_at = None
            try:
                await feed.connect()
                connected_at = time.monotonic()
                while self._running:
                    data = await feed.get_instrument_data()
                    self._on_message(data)
                    if time.monotonic() - connected_at >= _BACKOFF_RESET_AFTER_SECONDS:
                        backoff = _INITIAL_BACKOFF_SECONDS
            except Exception:
                pass
            finally:
                try:
                    await feed.disconnect()
                except Exception:
                    pass
            if not self._running:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    def _on_message(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        now = datetime.now(timezone.utc)
        msg_type = data.get("type")
        security_id = str(data.get("security_id", ""))

        if msg_type == "Ticker Data" and security_id == self._spot_security_id:
            with self._lock:
                self._spot_ltp = float(data["LTP"])
            self._staleness.touch("ws", now)

        elif msg_type == "Full Data" and security_id == self._futures_security_id:
            with self._lock:
                self._futures_ltp = float(data["LTP"])
                self._depth = _parse_depth(data.get("depth", []))
            self._staleness.touch("ws", now)


def _parse_depth(levels: list[dict[str, Any]]) -> MarketDepth:
    bids = [
        DepthLevel(price=float(level["bid_price"]), quantity=int(level["bid_quantity"]))
        for level in levels
    ]
    asks = [
        DepthLevel(price=float(level["ask_price"]), quantity=int(level["ask_quantity"]))
        for level in levels
    ]
    return MarketDepth(bid_levels=bids, ask_levels=asks)
