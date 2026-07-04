# Architecture Decision Record — TASK-019

**Directive:** `directives/TASK-019_ml-explainability.md`
**Status:** DRAFT
**Date:** 2026-07-04

## Problem

When TASK-018 flags a vector, name the 2–3 dimensions driving the anomaly, as a deterministic templated string — identical vector + model → identical string, and the explanation must be provably incapable of influencing the flag decision.

## Decision

Pure functions over the standardized vector TASK-018 already computed (`ScoreEvent.z_by_feature`). Ranking = descending `|z_i|`, tie-broken by `FEATURE_ORDER` position (total order → determinism even on exact ties). v1 uses plain per-feature |z| rather than Mahalanobis per-term contributions — covariance-aware attribution is strictly nicer but needs a stored covariance matrix per model version; ML Spec §5.3 explicitly allows either, and |z| keeps the registry schema and the mental model simpler. Recorded as the v2 upgrade path alongside the ensemble toggle (Open Decision #10).

Template follows TASK-010's discipline (fixed format string, values formatted to fixed precision, no free text):
`anomalous — driven by {name} ({z:+.1f}σ), {name} ({z:+.1f}σ)[, {name} ({z:+.1f}σ)] | score {score:.3f} vs flag {flag_threshold:.3f} | model v{version}`.
Clear-event string is the one-line equivalent. The decision NOT to route through `src/aeolus/explain/reason.py` itself is deliberate: that util is engine-owned and importing it from `aeolus.ml` is harmless, but extending it with ML templates would put ML vocabulary in an engine module — same style, separate home.

Separation proof: `top_contributors`/`anomaly_reason` take the flag outcome as *input* and return strings/lists only; nothing in `aeolus.ml.scorer` reads them before its threshold comparison. The QA gate includes a test asserting the scorer's flag decision is unchanged when explanation is monkey-patched to return garbage — explanation is provably downstream.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/ml/explain.py` | `top_contributors`, `anomaly_reason`, `clear_reason` |

## API Contracts

```python
def top_contributors(z_by_feature: dict[str, float], k: int = 3) -> list[tuple[str, float]]:
    """Descending |z|, ties broken by FEATURE_ORDER index. len <= k."""

def anomaly_reason(contributors: list[tuple[str, float]], score: float,
                   flag_threshold: float, model_version: int) -> str:
    """Deterministic template above. Raises ValueError on empty contributors."""

def clear_reason(score: float, clear_threshold: float, model_version: int) -> str: ...
```

## Performance / Failure Modes

Pure computation, negligible. Empty/absent z-vector cannot reach here on the happy path (TASK-018 only emits events with a populated vector); `anomaly_reason` still guards with ValueError so a future misuse fails loudly rather than posting an empty advisory.

## Definition of Done

- [ ] Property test: identical inputs → identical string (hypothesis)
- [ ] Ranking test incl. exact-tie determinism via FEATURE_ORDER
- [ ] Format pinned by golden-string tests (+/− sign, σ precision, model version)
- [ ] Scorer-independence test: garbage explanation ≠ changed flag decision
- [ ] Constraint check: templated, no free text, no LLM anywhere; explanation strictly downstream of decision
