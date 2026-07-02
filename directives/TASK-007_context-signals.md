# TASK-007 Context Signal Module

**Goal:** Compute Spec §6.5: yesterday's completed profile shape, gap type at open vs yesterday's value area, DTE relative to Tuesday-anchored NSE expiry (holiday-shift aware).

**Acceptance Criteria:**
- [ ] Standard contract: `(raw_value, reference_band, sub_score, reason_string)` per sub-signal
- [ ] DTE calculation is a clean, independently callable function — consumed by TASK-008 (config selection) and TASK-009 (outlook). Tuesday anchor, NSE holiday calendar, never hardcoded weekday
- [ ] Yesterday's profile-shape classification (trend day vs balanced/rotational) exposed as a standalone enum/flag — TASK-009's headline driver; must never exist only inside a blended sub-score
- [ ] Gap classification: gap-and-go vs gap-and-fill vs yesterday's value area
- [ ] Zero clock-based intraday interpretation anywhere in this module

**Inputs:** Prior-session data (TASK-001 storage), open prices (TASK-002), NSE holiday calendar; Spec §6.5, Build Prompt 7.

**Output:** `src/aeolus/signals/context.py`; standalone `dte()` + `prior_day_profile()` callables.

**Edge Cases:** expiry shifted by holiday; first session after go-live (no stored prior day); gap classification before value area is computable.

**Depends on:** TASK-001, TASK-002.

**Global constraints:** see `docs/CONSTRAINTS.md`.

**Status:** DRAFT
