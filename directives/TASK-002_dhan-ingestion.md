# TASK-002 Dhan API v2 Ingestion Layer

**Goal:** One ingestion module delivering NIFTY spot LTP, current-month NIFTY futures LTP, full option chain (OI, greeks, IV per strike), market depth, and GIFT Nifty price — with staleness surfaced as `system_status`.

**Acceptance Criteria:**
- [ ] Clear separation: live-feed (WebSocket) vs polled (REST) paths
- [ ] Reconnect/backoff logic on feed drop
- [ ] Heartbeat feeds `system_status` (`OK`/`STALE`/`DISCONNECTED`) — dropouts never silently degrade into a market-state read
- [ ] Futures price comes from the direct futures feed — NO synthetic future via put-call parity
- [ ] This module owns ALL staleness detection; downstream signal modules need none

**Inputs:** Dhan API v2 credentials/docs; Spec §2, Build Prompt 2.

**Output:** `src/aeolus/ingestion/` module; data contracts (typed models) consumed by signal modules.

**Edge Cases:** mid-session disconnect + recovery; partial option-chain response; GIFT Nifty feed availability pre-market; API rate limits on REST polling.

**Depends on:** TASK-001 (status enum exists).

**Notes:** Futures basis (futures − spot) optional, out of scope for v1 unless OPEN_DECISIONS #4 resolves otherwise.

**Global constraints:** see `docs/CONSTRAINTS.md`.

**Status:** DRAFT
