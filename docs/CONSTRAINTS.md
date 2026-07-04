# AEOLUS — Hard Build Constraints

These four constraints come from the spec and apply to **every module, every task, every PR**. A violation is a spec violation, not a style issue. Restated in every directive so they survive being read in isolation.

## 1. No per-signal veto

NO-GO must always emerge from the **composite score landing low** — never from a hardcoded rule on any single signal or category.

- ❌ `if gamma_score < X: force NO_GO`
- ✅ gamma's weakness flows into the weighted sum; the sum decides.

No category and no individual sub-signal has unilateral veto power. (Spec §5.2, §7; Build Prompt 8.)

## 2. No clock-time interpretation logic

No module may branch its signal logic on what time it currently is.

- The **scheduler** (Module 13) may gate *whether* the loop runs ("is the market open") — that's infra, not interpretation.
- Signals never decide *how to read* a value based on *when* it is.
- This is why "opening range" was reframed as the **volume-participation range** (first X% of cumulative session volume, not first N minutes) — see OPEN_DECISIONS #1.

(Spec §6.5, §7, §13; Build Prompt 13.)

## 3. Deterministic reason strings

Every explanation string is **templated** from `(raw_value, reference_band, sub_score)`. Same inputs → same string, always.

- Never LLM-narrated. Never free-text.
- Must trace back to a specific number crossing a specific threshold.
- Central templating utility (Module 10) — no per-module string-building duplication.

(Spec §10; Build Prompt 10.)

## 4. Polarity: GO = favorable for directional option BUYING

- **GO** = movement/trend developing, IV with room to expand, realized move likely to clear theta → conditions favor *buying* options.
- **NO-GO** = quiet/pinned → sit out.

This is the **inverse** of premium-selling regime tools that use the same words (where GO = low vol = safe to sell). Never copy polarity conventions from any reference tool without checking this. (Spec §5.2 polarity note.)

---

## Supporting invariants (also mandatory)

- **Hysteresis/debounce** before any state flip and any Discord post — N-cycle confirmation or margin crossing (Spec §7).
- **`system_status` ≠ `market_state`.** OK/STALE/DISCONNECTED is a data-integrity flag outside the three market states. A dead feed must never masquerade as a computed NO-GO (Spec §5.2). Staleness detection lives in the ingestion layer only (Build Prompt 2).
- **Direct futures feed** — never derive a synthetic future via put-call parity (Spec §2).
- **Tuesday expiry anchor, holiday-shift aware** — computed from NSE holiday calendar, never a hardcoded weekday (Spec §8).
- **`signal_snapshots` stores raw category data**, enough to recompute the composite retroactively if weights change — not just the final score (Build Prompt 1).

---

## ML overlay constraints (TASK-014..022, from `files/AEOLUS_ML_ANOMALY_SPEC.md`)

The anomaly ML module is an **advisory overlay**. Four additional hard constraints, restated in every ML directive:

1. **Advisory only.** The ML module never writes to engine tables, never changes NO_GO/PREPARE/GO, never gates a trade. Strictly read-only against `signal_snapshots`. The engine must run identically whether the module is present, absent, or broken (`Scheduler(ml_hooks=None)`).
2. **Cleanup-protected persistence.** Retention policy per OPEN_DECISIONS #6 (resolved 2026-07-04, supersedes ML Spec §6's blanket `ml_*` protection): `RetentionJob` (`src/aeolus/jobs/retention.py`, TASK-014) trims `signal_snapshots` + `ml_anomaly_scores` older than 90 days and prunes `ml_model_registry` to the last 30 versions/config — and must structurally refuse to touch **`ml_feature_store`** (the training corpus, absolutely protected), `state_transitions`, `daily_outlook`, `outcome_labels`. Ships a test proving protected row counts unchanged. End-of-day order is strict and never reordered: **feature-store sync → retrain → cleanup**.
3. **Isolation Forest decides; Mahalanobis/z-score only explains.** Per-feature attribution must never become (or influence) the flag decision.
4. **Deterministic explanations.** Top-feature attribution is templated from numbers (z-scores) — never LLM-narrated, never free text. Same vector + model → same string.

Supporting ML invariants:
- **Debounce + hysteresis on advisories** (ML Spec §7.1): post only on the transition into anomalous; separate enter (flag) / exit (clear) thresholds. Normal day = zero ML posts.
- **`STALE`/`DISCONNECTED` cycles are never scored or trained on** — a broken feed is not a market anomaly.
- **Two models, never one:** independent EXPIRY / NON_EXPIRY models, selected by the engine's existing config flag; `config_type` is a selector, not a feature.
- **Stored scaler only:** live scoring applies the training window's frozen μ/σ; never re-fit on a live vector.
- **Time-agnostic:** no clock-time conditioning in features or models (matches engine constraint #2).
