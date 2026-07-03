# TASK-002 Dhan API v2 Ingestion Layer

**Goal:** One ingestion module delivering NIFTY spot LTP, current-month NIFTY futures LTP, full option chain (OI, greeks, IV per strike), market depth, and GIFT Nifty price — with staleness surfaced as `system_status`.

For getting dhan credentials Follow [[DHAN Refresh token]] from obsidian valut
**Acceptance Criteria:**
- [x] Clear separation: live-feed (WebSocket) vs polled (REST) paths — `feed_ws.py` / `feed_rest.py`
- [x] Reconnect/backoff logic on feed drop — exponential backoff supervisor in `feed_ws.py`; code-reviewed against ADR, not yet exercised against a real live drop (QA follow-up, see debug report)
- [x] Heartbeat feeds `system_status` (`OK`/`STALE`/`DISCONNECTED`) — dropouts never silently degrade into a market-state read
- [x] Futures price comes from the direct futures feed — NO synthetic future via put-call parity
- [x] This module owns ALL staleness detection; downstream signal modules need none
- [x] Snapshot exposes `futures_basis` (futures_ltp − spot_ltp) as a raw field (OPEN_DECISIONS #4, resolved 2026-07-03 — include now, drift interpretation left to signal layer)

**Inputs:** Dhan API v2 credentials/docs; Spec §2, Build Prompt 2.

**Output:** `src/aeolus/ingestion/` module; data contracts (typed models) consumed by signal modules.

**Edge Cases:** mid-session disconnect + recovery (implemented via exponential-backoff supervisor, not live-fire tested); partial option-chain response (implemented — 2-consecutive-short-read detection before flagging `STALE`, avoids false alarms on a single short read); GIFT Nifty feed availability pre-market (resolved — Dhan API v2 has no GIFT City/NSE IX coverage at all, `gift_nifty` is permanently `None`, not a pre-market-only gap); API rate limits on REST polling (`IngestionService.latest()` is pull-based with no internal poll loop or rate limiter — cadence is entirely the caller's responsibility, which will be the scheduler in TASK-013; this module doesn't guess a rate limit).

**Depends on:** TASK-001 (status enum exists).

**Notes:** Futures basis (futures − spot) included in v1 per OPEN_DECISIONS #4 (resolved 2026-07-03) — raw field only, no drift/trend calc at this layer. Dhan credentials sourced from Supabase `api_keys` table (provider='DHAN'), populated/rotated by the existing external refresh daemon shared with the Ares project — see `~/Documents/Obsidian/Tools/Dhan Refresh token/`. No new credential-management scheme needed.

**Global constraints:** see `docs/CONSTRAINTS.md`.

**Status:** COMPLETE — merged to `main` (PR #3, commit `403138b`), 2026-07-03. ADR approved, implemented, live-verified, and merged same day (see `reports/debug/TASK-002_debug-report.md`).
