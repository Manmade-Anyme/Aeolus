# TASK-022 ML Drift Monitor + Ensemble Toggle (v2)

**Goal:** Lightweight drift note tracking the rolling anomaly-score baseline, plus an optional Isolation-Forest + Mahalanobis two-voter mode for the *decision* (not just explanation).

**Acceptance Criteria:**
- [ ] Rolling daily anomaly-score baseline; sustained upward shift surfaces as a low-priority note — never a per-cycle alert
- [ ] Ensemble voter mode: config toggle, OFF by default (Open Decision #10)
- [ ] No behavior change anywhere when both features are off

**Inputs:** ML Spec §5.5, §10 (decision 4); Build Prompt 9.

**Output:** `src/aeolus/ml/drift.py`.

**Edge Cases:** baseline shift caused by a genuine regime change vs model staleness (the note reports, humans judge).

**Depends on:** TASK-014..021 running in production with ≥ one full rolling window (60 trading days) of accumulated data.

**Global constraints:** see `docs/CONSTRAINTS.md` (incl. ML overlay constraints).

**Status:** DEFERRED (v2) — do not write ADR or build until the dependency condition is met and Open Decision #10 is resolved.
