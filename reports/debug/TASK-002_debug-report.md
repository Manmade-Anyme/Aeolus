# Debug Report — TASK-002

**Date:** 2026-07-03
**Verdict:** ✅ CLEAN

## What was run

- `pip install -e ".[dev]"` (added `dhanhq>=2.2.0`, `pandas>=1.4.3`)
- `python -m pytest tests/ -q` — full suite, `src/aeolus` package
- `python -m ruff check src tests`
- `python -m mypy src/aeolus`
- Manual scratch script hitting live Dhan REST (`option_chain`/`expiry_list`) to verify response-shape assumptions before writing the pytest version of the same check
- Live smoke test during NSE market hours (2026-07-03, ~10:14 IST) exercising the full `IngestionService` lifecycle: real Supabase credentials, real Dhan scrip master, real Dhan WebSocket, real Dhan REST

## Observed behavior

**Recurrence of the known `.pth` hidden-flag issue (see TASK-001 debug report).** Same root cause: hatchling's editable-install `.pth` file gets created with the macOS `UF_HIDDEN` flag, and CPython's `site.addpackage()` skips flagged `.pth` files, so `import aeolus` failed under both plain `python` and `pytest` after `pip install -e` was re-run to pick up the new `dhanhq`/`pandas` dependencies. TASK-001's report notes this recurs on every editable reinstall on this machine and was fixed each time with `chflags nohidden`. Applied the same fix here. Additionally added `pythonpath = ["src"]` to `[tool.pytest.ini_options]` in `pyproject.toml` — this makes `pytest` runs independent of the editable-install `.pth` state entirely, so a future reinstall recurrence no longer blocks the test suite (direct `python` script/scheduler runs still need the `chflags` fix, same as TASK-001).

**Instrument resolution confirmed against the live scrip master, not guessed.** Downloaded `https://images.dhan.co/api-data/api-scrip-master.csv` directly and inspected real rows before writing `instruments.py`: NIFTY 50 spot index is `SEM_SMST_SECURITY_ID=13` (`SEM_EXM_EXCH_ID=NSE`, `SEM_SEGMENT=I`, `SEM_INSTRUMENT_NAME=INDEX`, `SEM_TRADING_SYMBOL=NIFTY` exactly — other NIFTY-family indices like "NIFTY MIDCAP 150" have spaces and don't collide). Futures/options use `SEM_SEGMENT=D`, `SEM_INSTRUMENT_NAME` in `{FUTIDX, OPTIDX}`, `SEM_TRADING_SYMBOL` prefix `NIFTY-` (verified this prefix doesn't collide with `NIFTYNXT50-`, `BANKNIFTY-`, etc.). Live test on 2026-07-03 resolved the current-month future to `NIFTY-Jul2026-FUT` (expiry 2026-07-28) — correct, matches the current month.

**GIFT Nifty confirmed structurally absent from Dhan API v2**, not left as an assumption: the `dhanhq` v2.2.0 SDK's exchange-segment constants cover only `IDX_I`/`NSE_EQ`/`NSE_FNO`/`NSE_CURRENCY`/`BSE_EQ`/`BSE_FNO`/`MCX_COMM` — no NSE IX/GIFT City segment exists anywhere in the library. `gift_nifty` ships as a permanently-`None` field, excluded from `system_status` aggregation. ADR amended accordingly before implementation started (not discovered mid-build).

**Option chain response shape verified live**, not left as the "provisional" caveat originally written in `feed_rest.py`'s docstring: a real call against expiry 2026-07-07 returned 233 strikes with the assumed nesting (`data.data.oc[strike].ce/pe.{oi, implied_volatility, greeks.{delta,gamma,theta,vega}}`) intact — `feed_rest.py`'s parsing matched on the first try, no changes needed after the live check.

**Full live smoke test passed.** `IngestionService.start()` → real Supabase read of `api_keys` (provider=DHAN) → real scrip-master pull → real Dhan WebSocket auth/subscribe (spot Ticker + futures Full) → 5-second observation window received 29 live tick messages with zero parse exceptions → `latest()` produced a structurally valid `IngestionSnapshot` → `stop()` disconnected cleanly (`Connection closed!` from the SDK's own disconnect handshake). This is the first real exercise of the WS binary-packet parsing path (`process_ticker`/`process_full`/`process_market_depth` inside the third-party SDK) against live data, not just unit-level assumptions.

**Not exercised:** WS reconnect/backoff behavior under an actual live disconnect (would require deliberately killing the connection mid-session or waiting for a real drop) — the exponential-backoff supervisor logic in `feed_ws.py` is covered by code review against the ADR's design but not by a live-fire test. Flagged as a QA follow-up, not blocking, since the logic is a straightforward wrapper around well-understood asyncio retry semantics.

## Issues

| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| 1 | Low (env, not code) | Editable-install `.pth` created with macOS hidden flag, breaks `import aeolus` — same recurring issue as TASK-001 | n/a (venv artifact) | Fixed (`chflags nohidden`); additionally hardened by adding `pythonpath = ["src"]` to pytest config so test runs no longer depend on the editable-install `.pth` at all |
| 2 | Low | `pandas.read_csv` on the scrip master emitted a `DtypeWarning` (mixed types in `SEM_SERIES`/`SM_SYMBOL_NAME` columns, unused by resolution logic) | `src/aeolus/ingestion/instruments.py:50` | Fixed — `low_memory=False` |
| 3 | Low | mypy: `pandas`/`dhanhq` ship no type stubs | `pyproject.toml` | Fixed — added `ignore_missing_imports` override for both in `[[tool.mypy.overrides]]` |
| 4 | Low | mypy: `credentials.py` — Supabase's `.execute().data` is typed as generic JSON, not `dict`, so direct indexing didn't type-check | `src/aeolus/ingestion/credentials.py:41` | Fixed — `assert isinstance(row, dict)` before indexing |

## Constraint audit

- [x] No per-signal veto present — n/a, this task has no scoring/signal logic, only raw data + status
- [x] No clock-time branching in signal logic — `staleness.py` is elapsed-time-since-last-touch only (`now - last_seen`), never branches on wall-clock time; verified by `test_elapsed_time_not_wall_clock_drives_staleness`
- [x] Reason strings deterministic — n/a, no reason strings produced by this module
- [x] Polarity check: GO favors option buying — n/a, no market-state interpretation here
- [x] `system_status` never feeds `market_state` — this module produces `system_status` only; it has no concept of `market_state` at all, structurally impossible to conflate
- [x] Direct futures feed only, no synthetic put-call-parity derivation — confirmed by code inspection: `futures_ltp` comes solely from the WS `Full` subscription on the futures security_id; no parity calculation exists anywhere in the module
