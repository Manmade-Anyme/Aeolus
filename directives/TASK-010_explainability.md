# TASK-010 Explainability / Reason-String Templating

**Goal:** One small templating utility that every category module (TASK-003..007) and the composite level (TASK-008) call to produce reason strings — no string-building duplicated per module.

**Acceptance Criteria:**
- [x] Call signature: takes `(raw_value, reference_band, sub_score)` (+ signal identity), returns consistent reason string
- [x] Deterministic: same inputs → byte-identical string, every time (property test this)
- [x] Zero free-text / LLM generation — reason traces to a specific number crossing a specific threshold
- [x] Composite-level transition explanation cites the category/categories whose sub-score movement actually drove the flip

**Inputs:** Spec §10, Build Prompt 10.

**Output:** `src/aeolus/explain/`.

**Edge Cases:** missing/None raw value (string must say so explicitly, not fabricate); float formatting stability (rounding must be pinned so determinism holds).

**Depends on:** none structurally — build early (TASK-003 already needs its contract). May be built any time after TASK-002; signal modules may stub against its interface.

**Global constraints:** see `docs/CONSTRAINTS.md` — constraint #3 IS this module.

**Status:** COMPLETE — merged to `main` ([PR #12](https://github.com/dubeyshantanu2/Aeolus/pull/12), commit `ff680f9`), 2026-07-03. ADR approved, implemented, tested (see `reports/debug/TASK-010_debug-report.md`, `reports/qa/TASK-010_qa-report.md`), merged same day.
