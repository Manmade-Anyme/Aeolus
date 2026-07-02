# TASK-005 OI Structure Signal Module

**Goal:** Compute Spec §6.3: PCR level + rate of change, per-strike OI buildup classification, OI wall proximity + strength/decay, max-pain drift over the session.

**Acceptance Criteria:**
- [ ] Standard contract: `(raw_value, reference_band, sub_score, reason_string)` per sub-signal
- [ ] PCR scored on level AND rate of change (0.9→1.2 in an hour ≠ static 1.1)
- [ ] Buildup classification per strike from joint price+OI read: long buildup / short covering / short buildup / long unwinding
- [ ] Snapshot interval that buildup classification depends on is defined explicitly in the ADR
- [ ] Wall proximity both sides + wall strength/decay through session
- [ ] Max-pain drift tracked across session

**Inputs:** Option chain snapshots from TASK-002; Spec §6.3, Build Prompt 5.

**Output:** `src/aeolus/signals/oi_structure.py`.

**Edge Cases:** first snapshot of the day (no previous state for buildup classification); strikes entering/leaving the tracked window; OI update lag from exchange.

**Depends on:** TASK-002.

**Global constraints:** see `docs/CONSTRAINTS.md`.

**Status:** DRAFT
