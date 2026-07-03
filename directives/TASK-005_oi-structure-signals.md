# TASK-005 OI Structure Signal Module

**Goal:** Compute Spec §6.3: PCR level + rate of change, per-strike OI buildup classification, OI wall proximity + strength/decay, max-pain drift over the session.

**Acceptance Criteria:**
- [x] Standard contract: `(raw_value, reference_band, sub_score, reason_string)` per sub-signal
- [x] PCR scored on level AND rate of change (0.9→1.2 in an hour ≠ static 1.1) — level surfaced via `context`, ROC drives `sub_score`
- [x] Buildup classification per strike from joint price+OI read: long buildup / short covering / short buildup / long unwinding — price signal is `futures_ltp` direction (human decision 2026-07-03, resolving the ADR's blocking dependency), not spot or per-option premium
- [x] Snapshot interval that buildup classification depends on is defined explicitly in the ADR — "previous" = immediately preceding computation cycle's `IngestionSnapshot`, whatever cadence the caller runs at
- [x] Wall proximity both sides + wall strength/decay through session
- [x] Max-pain drift tracked across session

**Inputs:** Option chain snapshots from TASK-002; Spec §6.3, Build Prompt 5.

**Output:** `src/aeolus/signals/oi_structure.py`.

**Edge Cases:** first snapshot of the day (no previous state for buildup classification); strikes entering/leaving the tracked window; OI update lag from exchange.

**Depends on:** TASK-002.

**Global constraints:** see `docs/CONSTRAINTS.md`.

**Status:** IMPLEMENTED — ADR approved 2026-07-03 (blocking dependency resolved same day: futures-direction buildup classification, no ingestion amendment), code + tests complete (see `reports/debug/TASK-005_debug-report.md`, `reports/qa/TASK-005_qa-report.md`), pending PR/merge to `main` on `feature/TASK-005-oi-structure-signals`.
