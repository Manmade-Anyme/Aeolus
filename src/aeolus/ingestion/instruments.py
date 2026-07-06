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

import logging
import time
from dataclasses import dataclass
from datetime import date
from io import StringIO

import pandas as pd
import requests

logger = logging.getLogger(__name__)

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

_NSE = "NSE"
_INDEX_SEGMENT = "I"
_DERIVATIVES_SEGMENT = "D"
_NIFTY_PREFIX = "NIFTY-"
_INDIA_VIX_SYMBOL = "INDIA VIX"

# Only the columns the resolvers below actually filter/read. The full scrip
# master is ~27MB with dozens of columns; loading it whole with inferred
# dtypes OOM-killed the 512MB Fly VM (observed 2026-07-06, ~400MB RSS).
_REQUIRED_COLUMNS = [
    "SEM_EXM_EXCH_ID",
    "SEM_SEGMENT",
    "SEM_INSTRUMENT_NAME",
    "SEM_TRADING_SYMBOL",
    "SEM_SMST_SECURITY_ID",
    "SEM_EXPIRY_DATE",
    "SEM_LOT_UNITS",
]

_PARSE_CHUNK_ROWS = 50_000


class ScripMasterCache:
    """Last-known-good NIFTY subset of the scrip master, kept in Supabase.

    Dhan's CloudFront intermittently 403s the public CSV (observed 2026-07-06,
    ~10 min around market open, from Fly.io). The filtered subset is a few
    hundred rows, so a single-row table is enough. A day-stale copy is safe:
    spot/VIX security_ids are static and futures ids only change on roll day.

    All operations are best-effort — cache failures must never break a
    session that could otherwise proceed.
    """

    TABLE = "scrip_master_cache"

    def __init__(self, supabase_url: str, supabase_key: str) -> None:
        from supabase import create_client

        self._client = create_client(supabase_url, supabase_key)

    def save(self, df: pd.DataFrame) -> None:
        try:
            self._client.table(self.TABLE).upsert(
                {"id": 1, "csv_data": df.to_csv(index=False)}
            ).execute()
        except Exception:
            logger.warning("Failed to save scrip master cache (non-fatal)", exc_info=True)

    def load(self) -> pd.DataFrame | None:
        try:
            response = (
                self._client.table(self.TABLE)
                .select("csv_data, fetched_at")
                .eq("id", 1)
                .single()
                .execute()
            )
            row = response.data
            assert isinstance(row, dict)
            logger.warning(
                "Using cached scrip master from %s", row.get("fetched_at", "unknown")
            )
            return pd.read_csv(StringIO(row["csv_data"]), dtype=str)
        except Exception:
            logger.warning("Failed to load scrip master cache", exc_info=True)
            return None


@dataclass(frozen=True)
class ResolvedFutures:
    security_id: str
    expiry: date
    lot_size: int


class InstrumentResolver:
    """Resolves Dhan security_ids for NIFTY spot and current-month futures."""

    def __init__(
        self,
        scrip_master_url: str = SCRIP_MASTER_URL,
        cache: ScripMasterCache | None = None,
        max_attempts: int = 4,
        backoff_seconds: float = 15.0,
    ) -> None:
        self._url = scrip_master_url
        self._cache = cache
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._df: pd.DataFrame | None = None

    def refresh(self) -> None:
        """Re-pull the scrip master. Call once per session (roll-aware).

        Retries with exponential backoff (Dhan's CloudFront intermittently
        403s around market open), then falls back to the Supabase-cached
        subset from the last successful pull before giving up.
        """
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            if attempt:
                delay = self._backoff_seconds * 2 ** (attempt - 1)
                logger.warning(
                    "Scrip master fetch attempt %d/%d failed (%s); retrying in %.0fs",
                    attempt,
                    self._max_attempts,
                    last_error,
                    delay,
                )
                time.sleep(delay)
            try:
                self._df = self._fetch_and_filter()
            except requests.RequestException as error:
                last_error = error
                continue
            if self._cache is not None:
                self._cache.save(self._df)
            return

        logger.error(
            "Scrip master fetch failed after %d attempts: %s",
            self._max_attempts,
            last_error,
        )
        if self._cache is not None:
            cached = self._cache.load()
            if cached is not None:
                self._df = cached
                return
        assert last_error is not None
        raise last_error

    def _fetch_and_filter(self) -> pd.DataFrame:
        """Stream-download the CSV and keep only NIFTY-relevant rows.

        Parsed in chunks restricted to _REQUIRED_COLUMNS with dtype=str: the
        full file loaded via response.text + low_memory=False peaked ~400MB
        RSS and was OOM-killed on the 512MB Fly VM.
        """
        # Dhan's CDN 403s bare datacenter requests (e.g. from Fly.io);
        # browser-like headers are required for the public scrip master
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "text/csv,*/*",
        }
        with requests.get(self._url, headers=headers, timeout=60, stream=True) as response:
            response.raise_for_status()
            response.raw.decode_content = True
            kept: list[pd.DataFrame] = []
            for chunk in pd.read_csv(
                response.raw,
                usecols=_REQUIRED_COLUMNS,
                dtype=str,
                chunksize=_PARSE_CHUNK_ROWS,
            ):
                nse = chunk["SEM_EXM_EXCH_ID"] == _NSE
                index_rows = (chunk["SEM_SEGMENT"] == _INDEX_SEGMENT) & (
                    chunk["SEM_TRADING_SYMBOL"].isin(["NIFTY", _INDIA_VIX_SYMBOL])
                )
                futures_rows = (
                    (chunk["SEM_SEGMENT"] == _DERIVATIVES_SEGMENT)
                    & (chunk["SEM_INSTRUMENT_NAME"] == "FUTIDX")
                    & (chunk["SEM_TRADING_SYMBOL"].str.startswith(_NIFTY_PREFIX, na=False))
                )
                kept.append(chunk[nse & (index_rows | futures_rows)])
        return pd.concat(kept, ignore_index=True)

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
        return str(int(float(rows.iloc[0]["SEM_SMST_SECURITY_ID"])))

    def resolve_vix(self) -> str:
        df = self._frame()
        rows = df[
            (df["SEM_EXM_EXCH_ID"] == _NSE)
            & (df["SEM_SEGMENT"] == _INDEX_SEGMENT)
            & (df["SEM_TRADING_SYMBOL"] == _INDIA_VIX_SYMBOL)
        ]
        if rows.empty:
            raise LookupError("India VIX not found in scrip master")
        return str(int(float(rows.iloc[0]["SEM_SMST_SECURITY_ID"])))

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
            security_id=str(int(float(nearest["SEM_SMST_SECURITY_ID"]))),
            expiry=nearest["expiry_date"],
            lot_size=int(float(nearest["SEM_LOT_UNITS"])),
        )
