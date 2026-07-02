# TASK-004 Gamma Signal Module

**Goal:** Compute Spec §6.2: GEX / zero-gamma flip level — sign (dealer positioning regime) AND magnitude (conviction) — plus spot's distance from the flip level.

**Acceptance Criteria:**
- [ ] Standard contract: `(raw_value, reference_band, sub_score, reason_string)` per sub-signal
- [ ] Magnitude normalized (e.g. vs recent GEX magnitude history) so strong/weak negative gamma is a comparable score, not a raw dollar figure
- [ ] Sign and magnitude both contribute — weak negative gamma scores differently from strong negative gamma

**Inputs:** Option chain (OI, greeks per strike) from TASK-002; Spec §6.2, Build Prompt 4.

**Output:** `src/aeolus/signals/gamma.py`.

**Edge Cases:** flip level far outside traded strike range; thin OI making GEX noisy; early-session instability of the estimate.

**Depends on:** TASK-002.

**Global constraints:** see `docs/CONSTRAINTS.md`.

**Status:** DRAFT
