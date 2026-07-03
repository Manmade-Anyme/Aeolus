# TASK-008 Composite Scorer & State Machine

**Goal:** Combine TASK-003..007 outputs into the weighted composite score, map to NO_GO/PREPARE/GO via config-driven thresholds, apply hysteresis/debounce before any flip.

**Acceptance Criteria:**
- [x] Config loader selects expiry vs non-expiry weight/threshold table from TASK-007's DTE output (`config/profiles.py` — `EXPIRY_CONFIG`/`NON_EXPIRY_CONFIG`, ARES `pydantic-settings` pattern, not YAML — human-directed)
- [x] Composite = weighted sum of five category sub-scores; thresholds are calibration knobs in config, not code
- [x] Debounce: N-cycle confirmation before a state flips (Spec §7 — margin-crossing considered, not implemented for v1, see ADR)
- [x] Writes `signal_snapshots` row every cycle; `state_transitions` row ONLY on confirmed, debounced flips
- [x] ⛔ ZERO per-signal overrides. `if gamma_score < X: force NO_GO` is a spec violation — everything flows through the weighted sum. This module is where that constraint lives or dies.
- [x] `system_status` from TASK-002 passed through alongside — never mapped into market_state

**Inputs:** TASK-003..007 module outputs; config tables; Spec §7–8, Build Prompt 8. OPEN_DECISIONS #2 (binary vs graduated DTE weighting) resolved.

**Output:** `src/aeolus/engine/`; `config/tuning.py` + `config/profiles.py` (initial judgment-calibrated values, marked as such).

**Edge Cases:** category module returns error/missing (partial composite policy must be defined in ADR); score oscillating exactly at a threshold (debounce must provably prevent flapping); config file invalid at startup.

**Depends on:** TASK-003..007 ALL complete. Do not start earlier.

**Global constraints:** see `docs/CONSTRAINTS.md` — constraint #1 enforced here above all.

**Status:** COMPLETE — merged to `main` ([PR #10](https://github.com/dubeyshantanu2/Aeolus/pull/10), commit `72d8716`), 2026-07-03. ADR approved, implemented, tested (see `reports/debug/TASK-008_debug-report.md`, `reports/qa/TASK-008_qa-report.md`), merged same day.
