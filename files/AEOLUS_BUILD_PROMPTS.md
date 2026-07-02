# AEOLUS — Build Prompts
**Companion to AEOLUS_SYSTEM_SPEC.md**

## How to use this

Feed `AEOLUS_SYSTEM_SPEC.md` as shared context first — either attached to the PM
directive or pasted directly ahead of these prompts. The architecture decisions
in it are already resolved (this replaces most of what the Architect agent would
normally derive from a PM directive alone), so these prompts are written at
directive/ADR granularity: each is scoped to one component boundary with explicit
inputs, outputs, and constraints, ready for the Architect agent to turn into an
ADR, or for direct hand-off to Code Generator if you want to skip the ADR step
for a given module.

Build order matters — prompts are numbered in dependency order. Don't start 8
before 3–7 exist; don't start 11 before 8 exists.

Every prompt inherits four global constraints from the spec, restated here so
they survive being pasted individually:
- No hardcoded per-signal veto — NO-GO is a composite outcome only.
- No hardcoded intraday clock-time interpretation logic anywhere.
- Reason/explanation strings are templated and deterministic, never LLM-narrated.
- **Polarity: GO = favorable for directional option buying** (movement/IV
  expansion expected), **NO-GO = quiet/pinned, sit out.** Inverted from
  premium-selling regime tools — see Spec Section 5.2. Do not build against any
  reference tool's convention without checking this.

---

### 1. Supabase Schema & Migrations

**Objective:** Create the four tables from Spec Section 9 —
`signal_snapshots`, `state_transitions`, `daily_outlook`, `outcome_labels`.

**Deliverables:** migration files, indexes on timestamp + config_type, a
`system_status` enum (`OK`/`STALE`/`DISCONNECTED`) separate from the
`market_state` enum (`NO_GO`/`PREPARE`/`GO`).

**Constraints:** `signal_snapshots` must store enough raw category data to
recompute the composite retroactively if weights change later — don't just
store the final score.

---

### 2. Dhan API v2 Ingestion Layer

**Objective:** Live data client for NIFTY spot LTP, current-month NIFTY
futures LTP, full option chain (OI, greeks, IV per strike), market depth, plus
a GIFT Nifty price feed for the pre-market gap read.

**Deliverables:** a single ingestion module with clear separation between
live-feed (WebSocket) and polled (REST) data, reconnect/backoff logic, and a
heartbeat that feeds the `system_status` flag (Section 5.2 of spec — dropouts
must surface as `STALE`/`DISCONNECTED`, never silently degrade into a
market-state read). Use the direct futures feed for anything requiring a
futures price — do not derive a synthetic future via put-call parity.

**Constraints:** this module owns detecting data staleness; downstream signal
modules should not need their own staleness logic. Futures basis
(futures − spot) is optional and out of scope for v1 unless flagged otherwise
(Spec Section 14).

---

### 3. Volatility Signal Module

**Objective:** Compute Section 6.1 — IV percentile/rank, IV vs RV spread, VIX
level + rate of change, ATM straddle expected-move-consumed ratio.

**Deliverables:** one function per sub-signal, each returning
`(raw_value, reference_band, sub_score, reason_string)` — this tuple shape
should be the standard contract every category module follows, since it's what
feeds both the composite scorer and the explainability output directly.

**Constraints:** the expected-move-consumed ratio is live-only (needs realized
intraday move) and belongs here. The pre-market equivalent — ATM straddle
premium level vs its own recent-session history — is a separate function
consumed by Module 9 (Pre-Market Outlook), not by this module's live loop.

---

### 4. Gamma Signal Module

**Objective:** Compute Section 6.2 — GEX/zero-gamma flip level (sign +
magnitude) and spot's distance from it.

**Deliverables:** same `(raw_value, reference_band, sub_score, reason_string)`
contract as Module 3.

**Constraints:** magnitude must be normalized somehow (e.g. relative to recent
GEX magnitude history) so "strong" vs "weak" negative gamma is a meaningful,
comparable score — not just a raw dollar figure.

---

### 5. OI Structure Signal Module

**Objective:** Compute Section 6.3 — PCR level + rate of change, per-strike OI
buildup classification (long buildup / short covering / short buildup / long
unwinding), OI wall proximity + strength/decay, max pain drift.

**Deliverables:** same output contract as Module 3. OI buildup classification
needs current vs previous snapshot's price+OI joint state per strike — define
the snapshot interval this depends on explicitly.

---

### 6. Order Flow Signal Module

**Objective:** Compute Section 6.4 — CVD build direction + price divergence,
delta imbalance/absorption at range extremes, and the session-relative
volume-participation range (Open Decision 1 in the spec — confirm inclusion
before building this last piece).

**Constraints:** the volume-participation range must be computed as "range
formed by the first X% of cumulative session volume," not any fixed time
window. If Open Decision 1 is resolved as "drop it," this module ships with
just the first two sub-signals.

---

### 7. Context Signal Module

**Objective:** Compute Section 6.5 — yesterday's completed profile shape,
gap type at open vs yesterday's value area, DTE relative to Tuesday expiry
(NSE holiday-calendar aware).

**Deliverables:** the DTE calculation here is what Module 8 uses to select
expiry vs non-expiry config — make this a clean, independently callable
function, since the Pre-Market Outlook (Module 9) also needs the DTE flag.
Yesterday's profile-shape classification (trend day vs balanced day) must be
exposed as a clean, standalone enum/flag — this is what Module 9 uses as its
headline driver, so it cannot be buried only inside a blended sub-score.

---

### 8. Composite Scorer & State Machine

**Objective:** Combine Modules 3–7's outputs into the weighted composite score,
map it to NO-GO/PREPARE/GO via config-driven thresholds, apply hysteresis/
debounce before allowing a state flip.

**Deliverables:**
- A config loader that selects the expiry-day or non-expiry-day weight/threshold
  table based on Module 7's DTE output.
- The debounce logic (N-cycle confirmation or margin-based) from Spec Section 7.
- Emits a `state_transitions` row (Table 1) only on confirmed, debounced state
  changes — never on every cycle.

**Constraints:** this is the module that must never contain a per-signal
override. If you find yourself writing `if gamma_score < X: force NO_GO`,
that's a violation of the spec — it goes into the weighted sum instead.

---

### 9. Pre-Market Outlook Generator

**Objective:** Single-run job (not the continuous loop) producing the Section
5.1 probabilistic day-archetype forecast from GIFT Nifty gap, yesterday's
profile, ATM straddle premium level vs its recent-session history, IV
percentile/VIX heading in, OI/max-pain carryover, current-month futures price
context, and DTE.

**Deliverables:** writes one row to `daily_outlook` per session, with the
predicted archetype distribution, confidence, and the headline
prior-day-trend-exhaustion read stored as its own field — not just folded into
the blended score. Realized-archetype backfill is a separate job (Module 12),
not part of this one.

**Constraints:** if yesterday's completed profile (from Module 7) resolved as
a clean/elongated trend day, this must be surfaced as the lead line of the
Outlook output, ahead of the other contributing inputs — this is the specific
pattern the project exists to catch (Spec Section 1).

---

### 10. Explainability / Reason-String Templating

**Objective:** Standardize the reason-string generation referenced in every
category module's output contract (Modules 3–7) and used again for the
composite-level transition explanation.

**Deliverables:** a small templating utility, not a code-duplication of
string-building logic in every module — category modules should call into this
with their `(raw_value, reference_band, sub_score)` and get a consistent
reason string back.

**Constraints:** deterministic only — same inputs must always produce the same
reason string. No free-text generation.

---

### 11. Discord Output Formatter & Dispatch

**Objective:** Format and post the three message types from Spec Section 12 —
Pre-Market Outlook, state-transition, system-status alert.

**Deliverables:** state-transition messages must include the per-category
breakdown (reason strings from Module 10) and an explicit confirm/diverge note
against the current session's `daily_outlook` row.

**Constraints:** system-status alerts must be visually/structurally distinct
from market-state messages — this should never require reading carefully to
tell apart "market is dead" from "data feed is dead."

---

### 12. Outcome-Label Backfill Job

**Objective:** Section 9/11 enrichment job — runs after the fact (e.g. end of
day or on a delay), joins forward realized outcomes (straddle price change,
realized move, direction at +15/+30/+60 min) back onto `signal_snapshots` /
`state_transitions` rows, and backfills the realized archetype onto the
matching `daily_outlook` row.

**Deliverables:** writes to `outcome_labels`; updates `daily_outlook`'s
realized-archetype field.

**Constraints:** must not run live/synchronously with the scoring loop — this
is explicitly a look-back job, since the labels don't exist yet at signal-time.

---

### 13. Orchestration / Scheduler

**Objective:** Wire Modules 2–11 into a continuous loop running across the full
NSE session (9:15–3:30 IST), plus the single pre-open trigger for Module 9 and
the single post-close trigger for Module 12.

**Constraints:** the loop itself may be schedule-gated (only runs while the
market is open) — that's an infra/uptime decision, not a signal-interpretation
one. No module inside the loop should branch its own logic based on what time
it currently is; that responsibility stays entirely in the scheduler, and even
there it's just "is the market open," never "how should I read this signal
right now given the time."
