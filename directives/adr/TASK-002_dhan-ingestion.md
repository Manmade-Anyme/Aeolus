# Architecture Decision Record — TASK-002

**Directive:** `directives/TASK-002_dhan-ingestion.md`
**Status:** APPROVED
**Date:** 2026-07-03

## Problem

Build the single ingestion module that is the sole source of live market data for AEOLUS: NIFTY spot LTP, current-month NIFTY futures LTP, full option chain (OI, greeks, IV per strike), market depth, GIFT Nifty price, and `futures_basis` (OPEN_DECISIONS #4, resolved 2026-07-03 — included in v1 as a raw field). This module is also the **sole owner of staleness detection** — every downstream signal module (TASK-003..007) consumes its output and needs zero staleness logic of its own. `system_status` (`OK`/`STALE`/`DISCONNECTED`) computed here must never be confused with, or degrade into, `market_state` — those are structurally separate Postgres enums (TASK-001).

## Decision

Two data paths, matching the directive's acceptance criteria:

- **WebSocket (live feed):** spot LTP, futures LTP, market depth. These need continuous, event-driven updates — a five-second-stale spot price is a real problem for a signal engine re-scoring on every tick.
- **REST (polled):** full option chain (OI, greeks, IV per strike) and GIFT Nifty. Dhan's option-chain endpoint is rate-limited server-side (confirm exact limit against live Dhan v2 docs at implementation time — do not hardcode a guessed number into the poller without checking), so this path is a fixed-interval poller, not a stream.

**Credentials:** reuse the existing shared pattern from the Ares/Argus projects rather than inventing a new one. A standalone daemon (`~/Documents/Obsidian/Tools/Dhan Refresh token/`) already renews the Dhan 24-hour access token and writes `client_id`/`access_token` to a Supabase `api_keys` table (`provider = 'DHAN'`) on the same Supabase instance AEOLUS already uses for TASK-001. AEOLUS loads credentials from that table at startup and reloads on a `401` mid-session, exactly as the Ares client does. No Dhan secrets in AEOLUS's own `.env`. This also means AEOLUS has zero responsibility for token rotation — a single point of failure/ownership, already running, already proven live.

**Instrument/security-ID resolution:** Dhan identifies tradable instruments by numeric `security_id`, not by symbol, and these differ per expiry/strike and can change when Dhan updates its instrument master. Rather than hardcoding IDs, this module resolves them at startup (and on each session's first cycle, to catch the monthly futures roll and weekly option-chain roll) against Dhan's published instrument master CSV (`https://images.dhan.co/api-data/api-scrip-master.csv`, confirmed via the `dhanhq` SDK's own `Security.fetch_security_list`), cached in-process for the session. This keeps the futures-roll and strike-listing logic in one place instead of scattered magic numbers.

**SDK choice:** build on the official `dhanhq` v2.2.0 Python package rather than hand-rolling REST/WS calls — its `MarketFeed` class already implements the exact binary wire protocol (struct-level packet parsing for Ticker/Quote/Full packets), which is error-prone to reimplement and easy to get subtly wrong. One SDK quirk worth flagging: `MarketFeed` v2 does **not** support the standalone `Depth` (19) request code — only `Ticker`(15)/`Quote`(17)/`Full`(21) — so market depth is obtained by subscribing at `Full`, which bundles 5-level depth with LTP/volume/OI in one packet, rather than a dedicated depth-only subscription.

**GIFT Nifty — confirmed unavailable via Dhan API v2.** Verified by inspecting the official `dhanhq` v2.2.0 Python SDK source directly (not guessed): its exchange-segment constants cover `IDX_I`, `NSE_EQ`, `NSE_FNO`, `NSE_CURRENCY`, `BSE_EQ`, `BSE_FNO`, `MCX_COMM` only — no NSE IX / GIFT City segment exists anywhere in the SDK. GIFT Nifty trades on NSE International Exchange (GIFT City), which Dhan does not cover. `gift_nifty` is therefore a permanently `None`-capable field in v1, sourced from no feed at all, and excluded from `system_status` aggregation entirely (not "unavailable this session" — structurally absent). Revisit only if AEOLUS adds a second data vendor, which is out of scope here.

**`futures_basis`:** computed as `futures_ltp - spot_ltp` on every snapshot where both are fresh. Raw field only — no session-drift/trend calculation here; that's signal-layer interpretation (most likely TASK-007 context), scoped when that module's ADR is written. Consistent with "no per-signal veto" not applying here since this isn't a signal at all, just a derived raw value.

**Alternative considered:** single unified poller for everything (no WebSocket). Rejected — spot/futures/depth need sub-few-second freshness for a live composite re-score; polling REST for those would either hammer rate limits or silently lag, and the spec is explicit that staleness must be real, not assumed.

**Alternative considered:** synthetic futures via put-call parity as a fallback if the direct futures feed drops. Rejected outright — hard constraint (CONSTRAINTS.md #3-adjacent invariant, spec §2): direct futures feed only, never derived. If the futures feed is down, `futures_ltp` is `None` and `system_status` reflects that; it does not get backfilled synthetically.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/ingestion/credentials.py` | Load `client_id`/`access_token` from Supabase `api_keys` (provider='DHAN') at startup; reload on 401 mid-session |
| `src/aeolus/ingestion/instruments.py` | Resolve `security_id` for NIFTY spot, current-month futures (roll-aware), option chain strikes, GIFT Nifty (if it exists) against Dhan's instrument master; session-scoped cache |
| `src/aeolus/ingestion/feed_ws.py` | WebSocket client — spot LTP, futures LTP, market depth; reconnect/backoff; feeds heartbeat |
| `src/aeolus/ingestion/feed_rest.py` | REST poller — option chain (OI/greeks/IV per strike), GIFT Nifty; fixed interval respecting Dhan rate limits |
| `src/aeolus/ingestion/staleness.py` | Heartbeat tracking per data path → computes `system_status` |
| `src/aeolus/ingestion/models.py` | Typed data contracts (pydantic) — the only thing downstream modules import from this package |
| `src/aeolus/ingestion/service.py` | Wires WS + REST + staleness together; public `IngestionService` entrypoint used by the scheduler (TASK-013) and signal modules |
| `src/aeolus/ingestion/__init__.py` | Package marker, re-exports public models + `IngestionService` |

## API Contracts

```python
# src/aeolus/ingestion/models.py

class OptionStrike(BaseModel):
    strike: float
    call_oi: int
    put_oi: int
    call_iv: float
    put_iv: float
    call_greeks: dict[str, float]   # delta, gamma, theta, vega
    put_greeks: dict[str, float]

class MarketDepth(BaseModel):
    bid_levels: list[tuple[float, int]]   # (price, qty), best-first
    ask_levels: list[tuple[float, int]]

class IngestionSnapshot(BaseModel):
    """One row per ingestion cycle. Sole output of this module; consumed by TASK-003..007."""
    ts: datetime                      # timestamptz, UTC
    spot_ltp: float | None
    futures_ltp: float | None
    futures_basis: float | None       # futures_ltp - spot_ltp; None if either leg stale/missing
    depth: MarketDepth | None
    option_chain: list[OptionStrike]
    gift_nifty: float | None          # None if feed unavailable pre-market, or if Dhan has no GIFT Nifty coverage
    system_status: Literal["OK", "STALE", "DISCONNECTED"]
    system_status_detail: dict[str, str]   # per-path status, e.g. {"ws": "OK", "option_chain": "STALE", "gift_nifty": "UNAVAILABLE"}

# src/aeolus/ingestion/service.py

class IngestionService:
    async def start(self) -> None:
        """Authenticates, resolves instruments, opens WS, starts REST poller."""

    async def latest(self) -> IngestionSnapshot:
        """Returns current snapshot. Never raises on stale/disconnected feeds —
        that state is surfaced via system_status, not exceptions."""

    async def stop(self) -> None:
        """Clean shutdown of WS connection and poller task."""
```

Signal modules (TASK-003..007) import only `IngestionSnapshot` and its nested models — never touch `feed_ws.py`/`feed_rest.py`/`credentials.py` directly. `IngestionSnapshot` is not the standard `(raw_value, reference_band, sub_score, reason_string)` tuple — that contract belongs to signal *outputs*; this is signal *input*.

## Performance / Failure Modes

- **Reconnect/backoff (WS):** exponential backoff on disconnect (e.g. 1s, 2s, 4s, 8s... capped at 30s), resubscribing to all instruments on reconnect. Backoff resets after a sustained successful connection window.
- **Staleness thresholds:** heartbeat-based, per path, independently tracked in `system_status_detail`. Concrete thresholds (e.g. WS silence > Xs → `STALE`, > Ys → `DISCONNECTED`) to be tuned during implementation against observed live tick frequency — recorded in the debug report, not guessed here.
- **`system_status` aggregation:** worst-of across tracked paths, **except** GIFT Nifty if confirmed unavailable at the API level (not a runtime dropout) — that case is `UNAVAILABLE` in the detail map and excluded from the aggregate, since a feed that structurally doesn't exist isn't a data-integrity problem the way a WS drop is.
- **Partial option-chain response:** a snapshot with fewer strikes than expected is still emitted (not dropped) with whatever strikes came back; `system_status_detail["option_chain"]` reflects `STALE` if the poll returned less than the prior cycle's strike count for two consecutive cycles, not on a single short read (avoids false alarms on transient partial responses).
- **Rate limits:** `IngestionService.latest()` is pull-based — no internal poll loop, no internal rate limiter. Call cadence is entirely the caller's responsibility (the scheduler, TASK-013). This module doesn't guess a safe interval; whoever calls `.latest()` on a tight loop owns respecting Dhan's documented per-symbol option-chain rate limit.
- **No synthetic data ever** — every `None` in `IngestionSnapshot` is a real "don't know," not backfilled, not interpolated.

## Definition of Done

- [ ] Integration-style tests against `IngestionService.latest()` — live against real Dhan credentials pulled from Supabase, no internal mocking of the Dhan API itself (mocking the WS transport for reconnect-logic unit tests is fine; the end-to-end path must hit the real API at least once per QA pass)
- [ ] Reconnect test: kill WS mid-session, confirm reconnect + resubscribe, confirm `system_status` transitions `OK` → `DISCONNECTED` → `OK` without ever reading as a `market_state`
- [ ] Futures price confirmed sourced from direct futures feed (assert no put-call-parity code path exists at all)
- [ ] `futures_basis` present and correct on a snapshot with both legs live
- [x] GIFT Nifty existence verified — confirmed absent from Dhan API v2 (no NSE IX segment in the SDK); `gift_nifty` ships as a structurally-`None` field, excluded from `system_status`
- [ ] Partial option-chain response does not crash the service and does not falsely flip `system_status` on a single short cycle
- [ ] Constraint check: no per-signal veto (n/a — no scoring here), no clock-time branching (staleness is heartbeat-elapsed-time-based, not wall-clock-based — confirm thresholds are relative durations, not "before/after HH:MM"), deterministic reasons (n/a — this module has no reason strings, only raw data + status), polarity (n/a)

## ADR Amendment (2026-07-03) — India VIX + lot_size

**Trigger:** TASK-003 (volatility signals) needed India VIX, which `IngestionSnapshot` didn't carry; TASK-004 (gamma signals) needed NIFTY lot size. Both were flagged as blocking dependencies in the TASK-003/TASK-004 ADRs rather than silently reopening this already-merged module.

**Resolution — verified against live data before writing any code, not guessed:** re-pulled `https://images.dhan.co/api-data/api-scrip-master.csv` (the same compact CSV `instruments.py` already parses, no new file/dependency) and confirmed:
- India VIX: `SEM_TRADING_SYMBOL="INDIA VIX"`, `SEM_SEGMENT="I"`, `SEM_SMST_SECURITY_ID=21` — same `IDX_I` index segment NIFTY spot already subscribes through.
- `SEM_LOT_UNITS` column present on futures/option rows — NIFTY lot size = 65 as of 2026-07-03.

**Changes:**
- `models.py` — added `india_vix: float | None` to `IngestionSnapshot`.
- `instruments.py` — added `InstrumentResolver.resolve_vix() -> str`; added `lot_size: int` to `ResolvedFutures`.
- `feed_ws.py` — `LiveFeed` now subscribes a third `IDX`/`Ticker` instrument (VIX) alongside spot; new `latest_vix_ltp()`; VIX ticks feed the same `"ws"` staleness path as spot/futures (same WS connection, no new heartbeat key).
- `service.py` — resolves VIX security_id and lot_size at `start()`; exposes `IngestionService.lot_size: int | None` (session-scoped, resolved once, not part of the per-cycle `IngestionSnapshot` — lot size is static contract metadata, not a live market value, per TASK-004 ADR's reasoning); populates `india_vix` on every `latest()` call.

No changes to `system_status`/`system_status_detail` shape — VIX shares the existing `ws` key.

**Bonus finding, explicitly not acted on here:** the same live scrip-master pull also shows `DISPLAY_NAME="Gift Nifty"` at `SECURITY_ID=5024`, `EXCH_ID=NSE`, `SEGMENT=I` (from the *detailed* scrip master, `api-scrip-master-detailed.csv`) — which appears to contradict this ADR's `[x]` Definition-of-Done item above (GIFT Nifty confirmed absent, based on reading `dhanhq` SDK source constants, not live instrument data). **Not resolved as part of this amendment** — human explicitly scoped this pass to VIX + lot_size only. Whether `5024` is a real, live-ticking GIFT Nifty feed or a stale/placeholder listing is unverified. Flagging here so it isn't lost; recommend a follow-up check (live market hours) before either trusting or dismissing it.

**Tests:** `test_resolve_vix_is_india_vix_index` (new), `test_resolve_current_month_futures_is_unexpired_and_soonest` (extended to assert `lot_size > 0`), `test_models.py` (extended for `india_vix` present/`None` paths), `test_service_integration.py` (extended to assert `service.lot_size` populated live). Full suite 37/37 passing, `ruff`/`mypy` clean.
