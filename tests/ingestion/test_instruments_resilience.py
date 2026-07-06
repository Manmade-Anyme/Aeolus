"""Retry / cache-fallback / memory-safe parsing behavior of InstrumentResolver.

Covers the 2026-07-06 production incident: Dhan's CloudFront intermittently
403'd the scrip master around market open (fatal, no retry), and the full-file
pandas parse OOM-killed the 512MB Fly VM. All requests here are mocked; the
live-URL happy path stays in test_instruments_integration.py.
"""

from __future__ import annotations

import io
from datetime import date

import pandas as pd
import pytest
import requests

from aeolus.ingestion.instruments import InstrumentResolver

# Includes rows the filter must drop (BSE, OPTIDX) and an extra column the
# usecols-restricted parse must tolerate.
_CSV = """\
SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_SMST_SECURITY_ID,SEM_EXPIRY_DATE,SEM_LOT_UNITS,SEM_EXTRA_COLUMN
NSE,I,INDEX,NIFTY,13,,,ignored
NSE,I,INDEX,INDIA VIX,21,,,ignored
NSE,D,FUTIDX,NIFTY-Jul2026-FUT,53001,2026-07-28,65,ignored
NSE,D,FUTIDX,NIFTY-Aug2026-FUT,53002,2026-08-25,65,ignored
BSE,I,INDEX,NIFTY,999,,,ignored
NSE,D,OPTIDX,NIFTY-Jul2026-25000-CE,60001,2026-07-28,65,ignored
"""


class _RawStream(io.BytesIO):
    """BytesIO subclass so requests-style `raw.decode_content = True` works."""


class _FakeResponse:
    def __init__(self, status_code: int, body: str = "") -> None:
        self.status_code = status_code
        self.raw = _RawStream(body.encode())

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _FakeCache:
    """Duck-typed stand-in for ScripMasterCache recording save/load calls."""

    def __init__(self, cached_csv: str | None = None) -> None:
        self.saved: list[pd.DataFrame] = []
        self._cached_csv = cached_csv

    def save(self, df: pd.DataFrame) -> None:
        self.saved.append(df)

    def load(self) -> pd.DataFrame | None:
        if self._cached_csv is None:
            return None
        return pd.read_csv(io.StringIO(self._cached_csv), dtype=str)


def _patch_responses(monkeypatch, responses: list[_FakeResponse]) -> list[int]:
    """requests.get returns the next canned response per call; returns call log."""
    calls: list[int] = []

    def fake_get(url, **kwargs):
        calls.append(len(calls) + 1)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


def test_fetch_keeps_only_nifty_relevant_rows(monkeypatch):
    _patch_responses(monkeypatch, [_FakeResponse(200, _CSV)])
    resolver = InstrumentResolver(backoff_seconds=0)
    resolver.refresh()

    assert resolver.resolve_spot() == "13"
    assert resolver.resolve_vix() == "21"
    futures = resolver.resolve_current_month_futures(date(2026, 7, 6))
    assert futures.security_id == "53001"
    assert futures.expiry == date(2026, 7, 28)
    assert futures.lot_size == 65
    # BSE NIFTY row and OPTIDX row must have been filtered out at parse time
    assert len(resolver._frame()) == 4


def test_transient_403_is_retried_until_success(monkeypatch):
    calls = _patch_responses(
        monkeypatch,
        [_FakeResponse(403), _FakeResponse(403), _FakeResponse(200, _CSV)],
    )
    resolver = InstrumentResolver(backoff_seconds=0)
    resolver.refresh()

    assert len(calls) == 3
    assert resolver.resolve_spot() == "13"


def test_exhausted_retries_fall_back_to_cache(monkeypatch):
    calls = _patch_responses(monkeypatch, [_FakeResponse(403)])
    cache = _FakeCache(cached_csv=_CSV)
    resolver = InstrumentResolver(cache=cache, max_attempts=2, backoff_seconds=0)
    resolver.refresh()

    assert len(calls) == 2
    assert cache.saved == []  # nothing fetched, nothing to save
    assert resolver.resolve_spot() == "13"
    assert resolver.resolve_vix() == "21"


def test_exhausted_retries_without_cache_raise(monkeypatch):
    _patch_responses(monkeypatch, [_FakeResponse(403)])
    resolver = InstrumentResolver(max_attempts=2, backoff_seconds=0)

    with pytest.raises(requests.HTTPError):
        resolver.refresh()


def test_successful_fetch_updates_cache(monkeypatch):
    _patch_responses(monkeypatch, [_FakeResponse(200, _CSV)])
    cache = _FakeCache()
    resolver = InstrumentResolver(cache=cache, backoff_seconds=0)
    resolver.refresh()

    assert len(cache.saved) == 1
    assert len(cache.saved[0]) == 4
