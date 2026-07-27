"""DhanRedisClient adapter for Aeolus (TASK-002 / Dhan Redis Hub Migration).
Connects to central Dhan Redis Hub microservice (DHAN_HUB_URL) to fetch cached
option chains, expiry lists, and market quotes with sub-millisecond latency.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("aeolus.redis_client")


class DhanRedisClient:
    """Client adapter library for Aeolus.
    Connects to central Dhan Redis Hub REST proxy (DHAN_HUB_URL).
    """

    def __init__(self, hub_url: str | None = None) -> None:
        url = hub_url or os.getenv("DHAN_HUB_URL", "https://dhan-redis-hub.fly.dev")
        self.hub_url = url.rstrip("/")

    def get_expiry_list(
        self,
        symbol: str = "NIFTY",
        underlying_scrip: int = 13,
        underlying_seg: str = "IDX_I",
    ) -> list[str] | None:
        """Retrieves expiry list for an underlying from Dhan Redis Hub."""
        try:
            resp = httpx.post(
                f"{self.hub_url}/expirylist",
                json={
                    "symbol": symbol.upper(),
                    "underlying_scrip": underlying_scrip,
                    "underlying_seg": underlying_seg,
                },
                timeout=10.0,
            )
            if resp.status_code == 200 and isinstance(resp.json(), list):
                return resp.json()
        except Exception as exc:
            logger.warning("DhanRedisClient get_expiry_list error: %s", exc)
        return None

    def get_option_chain(
        self,
        symbol: str = "NIFTY",
        expiry: str = "",
        underlying_scrip: int = 13,
        underlying_seg: str = "IDX_I",
    ) -> dict[str, Any] | None:
        """Retrieves raw option chain dictionary from Dhan Redis Hub."""
        try:
            resp = httpx.post(
                f"{self.hub_url}/optionchain",
                json={
                    "symbol": symbol.upper(),
                    "underlying_scrip": underlying_scrip,
                    "underlying_seg": underlying_seg,
                    "expiry": expiry,
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                oc = (
                    data.get("data", {}).get("oc")
                    or data.get("oc")
                    or (
                        data.get("data", {}).get("data", {}).get("oc")
                        if isinstance(data.get("data"), dict)
                        else None
                    )
                )
                if oc and isinstance(oc, dict):
                    return oc
        except Exception as exc:
            logger.warning("DhanRedisClient get_option_chain error: %s", exc)
        return None

    def get_quote(
        self, security_id: int | str, exchange_segment: str = "NSE_EQ"
    ) -> dict[str, Any] | None:
        """Retrieves market quote for a security from Dhan Redis Hub."""
        try:
            resp = httpx.post(
                f"{self.hub_url}/quote",
                json={
                    "security_id": str(security_id),
                    "exchange_segment": exchange_segment,
                },
                timeout=10.0,
            )
            if resp.status_code == 200 and isinstance(resp.json(), dict):
                return resp.json()
        except Exception as exc:
            logger.warning("DhanRedisClient get_quote error: %s", exc)
        return None
