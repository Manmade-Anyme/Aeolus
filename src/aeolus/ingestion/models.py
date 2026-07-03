"""Typed data contracts for the ingestion layer (TASK-002).

IngestionSnapshot is the only thing signal modules (TASK-003..007) import from
this package. It is NOT the standard (raw_value, reference_band, sub_score,
reason_string) tuple — that contract is for signal *outputs*; this is signal
*input*. Every optional field being None is a real "don't know", never
backfilled or interpolated.
"""

from __future__ import annotations

from datetime import datetime
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
    system_status: SystemStatus
    system_status_detail: dict[str, PathStatus]  # per-path, e.g. {"ws": "OK", "option_chain": "STALE"}
