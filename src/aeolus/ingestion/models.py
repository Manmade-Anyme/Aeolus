"""Typed data contracts for the ingestion layer (TASK-002).

IngestionSnapshot is the only thing signal modules (TASK-003..007) import from
this package. It is NOT the standard (raw_value, reference_band, sub_score,
reason_string) tuple — that contract is for signal *outputs*; this is signal
*input*. Every optional field being None is a real "don't know", never
backfilled or interpolated.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

SystemStatus = Literal["OK", "STALE", "DISCONNECTED"]
PathStatus = Literal["OK", "STALE", "DISCONNECTED", "UNAVAILABLE"]


class Greeks(BaseModel):
    delta: float
    gamma: float
    theta: float
    vega: float


class OptionStrike(BaseModel):
    strike: float
    call_oi: int
    put_oi: int
    call_iv: float
    put_iv: float
    call_greeks: Greeks
    put_greeks: Greeks

    @property
    def has_valid_call_greeks(self) -> bool:
        """True if call leg does not have open interest with zeroed gamma."""
        return not (self.call_oi > 0 and self.call_greeks.gamma == 0.0)

    @property
    def has_valid_put_greeks(self) -> bool:
        """True if put leg does not have open interest with zeroed gamma."""
        return not (self.put_oi > 0 and self.put_greeks.gamma == 0.0)

    @property
    def has_valid_greeks(self) -> bool:
        """True if neither call nor put leg has open interest with zeroed gamma.

        Dhan's option chain endpoint returns 0 for greeks and IV on illiquid
        strikes with no recent trades, even when those strikes carry substantial
        open interest. Including them with gamma=0 distorts net GEX calculations.
        """
        return self.has_valid_call_greeks and self.has_valid_put_greeks




class DepthLevel(BaseModel):
    price: float
    quantity: int


class MarketDepth(BaseModel):
    bid_levels: list[DepthLevel]
    ask_levels: list[DepthLevel]


class IngestionSnapshot(BaseModel):
    """One row per ingestion cycle. Sole output of this module."""

    ts: datetime  # timestamptz, UTC
    spot_ltp: float | None
    futures_ltp: float | None
    futures_basis: float | None  # futures_ltp - spot_ltp; None if either leg stale/missing
    depth: MarketDepth | None
    option_chain: list[OptionStrike]
    india_vix: float | None  # NSE index, security_id=21, same IDX_I segment/path as spot_ltp
    gift_nifty: float | None  # structurally None — Dhan API v2 has no GIFT City/NSE IX coverage
    volume: int | None  # futures leg, exchange's own cumulative-since-session-start counter
    total_buy_quantity: int | None  # futures leg, from the same Full packet as volume
    total_sell_quantity: int | None  # futures leg, from the same Full packet as volume
    day_high: float | None  # futures leg session high, from the same Full packet
    day_low: float | None  # futures leg session low, from the same Full packet
    expiry_date: date | None  # nearest NIFTY option expiry, from Dhan's expiry_list endpoint
    # (TASK-002 amendment #3, TASK-007 ADR) -- already holiday-shift aware; resolved once per
    # session by IngestionService.start(), never recomputed from a calendar in this module
    system_status: SystemStatus
    system_status_detail: dict[str, PathStatus]  # per-path, e.g. {"ws": "OK", "option_chain": "STALE"}
