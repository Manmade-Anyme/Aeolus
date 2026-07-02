# TASK-006 Order Flow Signal Module

**Goal:** Compute Spec §6.4: CVD build direction + divergence from price, delta imbalance/absorption at range extremes, session-relative volume-participation range.

**Acceptance Criteria:**
- [ ] Standard contract: `(raw_value, reference_band, sub_score, reason_string)` per sub-signal
- [ ] Price progress without CVD confirmation flagged as fragile move (divergence signal)
- [ ] Volume-participation range = range formed by first X% of cumulative session volume — NEVER a fixed time window
- [ ] ⚠️ GATE: OPEN_DECISIONS #1 must be resolved before building the volume-participation piece. If resolved "drop," ship with the first two sub-signals only.

**Inputs:** Market depth / trade feed from TASK-002; Spec §6.4, Build Prompt 6.

**Output:** `src/aeolus/signals/order_flow.py`.

**Edge Cases:** low-volume open making the X% range degenerate; CVD reset semantics across reconnects (must not double-count after TASK-002 reconnect).

**Depends on:** TASK-002; OPEN_DECISIONS #1 resolution.

**Global constraints:** see `docs/CONSTRAINTS.md` — the no-clock rule is why this range is volume-based.

**Status:** DRAFT — blocked partially on OPEN_DECISIONS #1
