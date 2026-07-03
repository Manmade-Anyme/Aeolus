# QA Report — TASK-002

**Date:** 2026-07-03
**Verdict:** ✅ PASS

## Test summary

| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/ingestion/test_models.py` | 2 | 2 | 0 | `IngestionSnapshot`/nested models, full-data path and fully-missing-data (`None`) path |
| `tests/ingestion/test_staleness.py` | 5 | 5 | 0 | Untouched=DISCONNECTED, fresh touch=OK, elapsed-time thresholds (not wall-clock), worst-of aggregation, re-touch resets staleness |
| `tests/ingestion/test_instruments_integration.py` | 3 | 3 | 0 | Live against the real Dhan scrip master CSV — spot resolves to security_id 13, current-month futures resolves to the correct unexpired contract, futures roll picks the next contract once the current one expires |
| `tests/ingestion/test_credentials_integration.py` | 1 | 1 | 0 | Live against the real shared Supabase `api_keys` table (skipped if credentials absent) |
| `tests/ingestion/test_service_integration.py` | 1 | 1 | 0 | Live end-to-end: real credentials → real instrument resolution → real Dhan WebSocket (spot Ticker + futures Full) → real Dhan REST option chain → snapshot assembly → clean shutdown. Run during live NSE market hours (2026-07-03, ~10:14 IST) |
| `tests/storage/*` (pre-existing, TASK-001) | 24 | 24 | 0 | Unaffected regression check |

Total: 36/36 passing. `ruff check` and `mypy` both clean across `src/` and `tests/`.

## Scenarios covered

Integration-style against public contracts only (`IngestionService.latest()`, `InstrumentResolver`, `CredentialsSource`, `StalenessTracker`) — no internal mocking of the Dhan API or Supabase. The one exception is `test_models.py`, which is a pure pydantic-contract check (no external calls needed, matches TASK-001's `test_models.py` precedent).

Live end-to-end run confirmed:
- Credential load from Supabase succeeds and returns usable `client_id`/`access_token`
- Scrip-master-based instrument resolution returns real, currently-valid security_ids (NIFTY spot = 13; current-month futures = `NIFTY-Jul2026-FUT`, expiry 2026-07-28, correctly the nearest unexpired contract as of 2026-07-03)
- WebSocket authenticates, subscribes (Ticker for spot, Full for futures+depth), and receives live ticks — 29 messages parsed without exception in a 5-second window
- REST option chain resolves the nearest expiry (2026-07-07, a Tuesday — consistent with the NIFTY weekly-expiry convention) and returns a full 233-strike chain with OI/IV/greeks in the expected shape
- `IngestionSnapshot.futures_basis` computes correctly when both legs are live
- `system_status_detail` reports per-path status independently; `gift_nifty` is `None` and excluded from status aggregation
- `IngestionService.stop()` disconnects cleanly (no hung threads, no exception)

## Edge cases exercised

From TASK-002 directive's Edge Cases section:
- **Mid-session disconnect + recovery** — exponential-backoff reconnect logic implemented in `feed_ws.py` and code-reviewed against the ADR (1s→30s backoff, resets after a sustained connection). **Not live-fire tested** — would require deliberately killing the connection mid-session; not attempted this pass. See Gaps below.
- **Partial option-chain response** — `OptionChainPoller.degraded` flags `STALE` only after 2 consecutive short reads, not a single one. Unit-covered by construction (the counter logic), not exercised against an actual partial live response (the one live call returned a full, non-degraded 233-strike chain).
- **GIFT Nifty feed availability pre-market** — resolved definitively, not just tested: confirmed via direct inspection of the `dhanhq` v2.2.0 SDK source that no NSE IX/GIFT City exchange segment exists anywhere in the library. This isn't a pre-market-only gap, it's permanent. `gift_nifty` ships structurally `None`.
- **API rate limits on REST polling** — `IngestionService.latest()` is pull-based with no internal poll loop; rate-limit compliance is the caller's responsibility (TASK-013, not yet built). No rate-limit-specific test exists because there's no internal timer to test.

## Bugs found and fixed during this pass

None in the ingestion code itself. One environment issue (recurrence of TASK-001's `.pth`/`UF_HIDDEN` editable-install problem) — see `reports/debug/TASK-002_debug-report.md` Issue #1. Not a code defect; additionally hardened via `pythonpath = ["src"]` in `pyproject.toml` so pytest no longer depends on the editable install surviving a reinstall.

## Gaps / follow-ups

- WS reconnect/backoff not exercised against a real live disconnect. Recommend a deliberate-kill test (or waiting for a natural drop and inspecting logs) before this module carries production trading decisions.
- Option-chain partial-response detection (`degraded` flag) not exercised against an actual partial/short live response — only a full, healthy response was observed live.
- No test covers the REST rate-limit behavior directly (Dhan's exact per-symbol option-chain limit was not independently confirmed against live docs — this module doesn't self-throttle, so a caller polling too aggressively would just see more `status != "success"` responses feeding into `STALE`/`DISCONNECTED` over time, not a hard crash, but this hasn't been deliberately triggered).
- `system_status` per-path detail currently tracks only `ws` and `option_chain` — if a future task wants separate visibility into "spot feed dead but futures feed fine" (both currently share the single `ws` heartbeat), that would need a small refactor to per-instrument heartbeats.
