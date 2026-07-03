# TASK-011 Discord Output Formatter & Dispatch

**Goal:** Format and post the three Spec §12 message types via webhook: Pre-Market Outlook, state-transition, system-status alert.

**Acceptance Criteria:**
- [x] Outlook message: archetype forecast, confidence, key contributing inputs (trend-exhaustion lead line when applicable)
- [x] State-transition message: new state, composite score, per-category breakdown with TASK-010 reason strings, explicit confirm/diverge note vs current session's `daily_outlook` row
- [x] System-status alert: terse, visually/structurally DISTINCT from market-state messages (separate channel or unmistakable format) — "market is dead" vs "feed is dead" must never require careful reading to distinguish
- [x] Posts only on genuine debounced transitions (TASK-008 already gates this; this module must not add its own state logic)

**Inputs:** TASK-008 transitions, TASK-009 outlook rows, TASK-010 strings; Spec §12, Build Prompt 11.

**Output:** `src/aeolus/output/`.

**Edge Cases:** webhook failure/retry (must not drop a transition silently, must not double-post on retry); Discord rate limits; message length limits with full per-category breakdown.

**Depends on:** TASK-008, TASK-009, TASK-010.

**Global constraints:** see `docs/CONSTRAINTS.md`.

**Status:** COMPLETE — merged to `main` ([PR #13](https://github.com/dubeyshantanu2/Aeolus/pull/13), commit `87d992e`), 2026-07-03. ADR approved, implemented, tested (see `reports/debug/TASK-011_debug-report.md`, `reports/qa/TASK-011_qa-report.md`), merged same day.
