"""Security-ID resolution against Dhan's published instrument master (TASK-002 ADR).

Dhan identifies instruments by numeric security_id, not symbol, and futures/
option security_ids change every roll. Resolved once per session (not per
cycle) against the compact scrip master CSV — cheap pull, keeps the
futures-roll logic in one place instead of scattered magic numbers.

Column names/values below were confirmed against a live pull of
https://images.dhan.co/api-data/api-scrip-master.csv (2026-07-03), not
guessed: SEM_EXM_EXCH_ID="NSE", SEM_SEGMENT="I" for the index / "D" for
derivatives, SEM_INSTRUMENT_NAME in {"INDEX","FUTIDX","OPTIDX"},
SEM_TRADING_SYMBOL "NIFTY" (index) / "NIFTY-<Mon><Year>-FUT" (futures) /
"NIFTY-<Mon><Year>-<strike>-CE|PE" (options).

India VIX confirmed present in the same file, same segment: SEM_SEGMENT="I",
SEM_TRADING_SYMBOL="INDIA VIX" (2026-07-03) -- same IDX_I subscription path
as spot, no new segment/vendor needed (TASK-003 ADR blocking dependency #1).

SEM_LOT_UNITS confirmed present on futures/option rows (65 for NIFTY,
2026-07-03) -- resolved here instead of hardcoded in signal modules
(TASK-004 ADR's gex_regime needs it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import StringIO

import pandas as pd
import requests

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

_NSE = "NSE"
_INDEX_SEGMENT = "I"
_DERIVATIVES_SEGMENT = "D"
_NIFTY_PREFIX = "NIFTY-"
_INDIA_VIX_SYMBOL = "INDIA VIX"


@dataclass(frozen=True)
class ResolvedFutures:
    security_id: str
    expiry: date
    lot_size: int


class InstrumentResolver:
    """Resolves Dhan security_ids for NIFTY spot and current-month futures."""

    def __init__(self, scrip_master_url: str = SCRIP_MASTER_URL) -> None:
        self._url = scrip_master_url
        self._df: pd.DataFrame | None = None

    def refresh(self) -> None:
        """Re-pull the scrip master. Call once per session (roll-aware)."""
        # Dhan's CDN 403s bare datacenter requests (e.g. from Fly.io);
        # browser-like headers are required for the public scrip master
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "text/csv,*/*",
        }
        response = requests.get(self._url, headers=headers, timeout=30)
        response.raise_for_status()
        self._df = pd.read_csv(StringIO(response.text), low_memory=False)

    def _frame(self) -> pd.DataFrame:
        if self._df is None:
            self.refresh()
        assert self._df is not None
        return self._df

    def resolve_spot(self) -> str:
        df = self._frame()
        rows = df[
            (df["SEM_EXM_EXCH_ID"] == _NSE)
            & (df["SEM_SEGMENT"] == _INDEX_SEGMENT)
            & (df["SEM_INSTRUMENT_NAME"] == "INDEX")
            & (df["SEM_TRADING_SYMBOL"] == "NIFTY")
        ]
        if rows.empty:
            raise LookupError("NIFTY spot index not found in scrip master")
        return str(int(rows.iloc[0]["SEM_SMST_SECURITY_ID"]))

    def resolve_vix(self) -> str:
        df = self._frame()
        rows = df[
            (df["SEM_EXM_EXCH_ID"] == _NSE)
            & (df["SEM_SEGMENT"] == _INDEX_SEGMENT)
            & (df["SEM_TRADING_SYMBOL"] == _INDIA_VIX_SYMBOL)
        ]
        if rows.empty:
            raise LookupError("India VIX not found in scrip master")
        return str(int(rows.iloc[0]["SEM_SMST_SECURITY_ID"]))

    def resolve_current_month_futures(self, as_of: date) -> ResolvedFutures:
        df = self._frame()
        rows = df[
            (df["SEM_EXM_EXCH_ID"] == _NSE)
            & (df["SEM_SEGMENT"] == _DERIVATIVES_SEGMENT)
            & (df["SEM_INSTRUMENT_NAME"] == "FUTIDX")
            & (df["SEM_TRADING_SYMBOL"].str.startswith(_NIFTY_PREFIX))
        ].copy()
        if rows.empty:
            raise LookupError("NIFTY futures not found in scrip master")
        rows["expiry_date"] = pd.to_datetime(rows["SEM_EXPIRY_DATE"]).dt.date
        unexpired = rows[rows["expiry_date"] >= as_of].sort_values("expiry_date")
        if unexpired.empty:
            raise LookupError("No unexpired NIFTY futures contract found")
        nearest = unexpired.iloc[0]
        return ResolvedFutures(
            security_id=str(int(nearest["SEM_SMST_SECURITY_ID"])),
            expiry=nearest["expiry_date"],
            lot_size=int(nearest["SEM_LOT_UNITS"]),
        )
