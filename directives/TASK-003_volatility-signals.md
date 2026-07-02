# TASK-003 Volatility Signal Module

**Goal:** Compute Spec §6.1 sub-signals: IV percentile/rank (20–60d trailing), IV vs RV spread, India VIX level + rate of change, ATM straddle expected-move-consumed ratio.

**Acceptance Criteria:**
- [ ] One function per sub-signal, each returning `(raw_value, reference_band, sub_score, reason_string)` — this is the standard contract for ALL category modules (TASK-003..007)
- [ ] Expected-move-consumed ratio = realized move so far ÷ straddle-implied expected move; live-only, in this module's loop
- [ ] Pre-market equivalent (straddle premium level vs 10–20 session history) is a SEPARATE function consumed by TASK-009, not by this module's live loop
- [ ] Reason strings via TASK-010 util (or interim stub matching its contract)

**Inputs:** TASK-002 data contracts; Spec §6.1, Build Prompt 3.

**Output:** `src/aeolus/signals/volatility.py` (or package).

**Edge Cases:** insufficient trailing history early after go-live; IV missing for a strike; VIX unavailable.

**Depends on:** TASK-002.

**Global constraints:** see `docs/CONSTRAINTS.md` — esp. no clock logic, deterministic reasons.

**Status:** DRAFT
