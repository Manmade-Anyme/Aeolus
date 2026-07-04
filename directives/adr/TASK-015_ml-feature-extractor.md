# Architecture Decision Record — TASK-015

**Directive:** `directives/TASK-015_ml-feature-extractor.md`
**Status:** DRAFT
**Date:** 2026-07-04

## Problem

A stored Isolation Forest is only valid against vectors built with the exact feature set, order, and scaling it trained on. We need a pure, deterministic `SignalSnapshot → vector` transform with a frozen, versioned feature order, and a standardize step that only ever *applies* a stored scaler.

## Decision

One module of pure functions, no I/O, no state. `FEATURE_ORDER` is a frozen tuple of feature names; `FEATURE_SET_VERSION` is an int bumped on ANY change to the tuple or to an accessor's semantics. Both are stamped into every `MLFeatureRow` and `MLModelVersion`; the scorer (TASK-018) refuses to score when versions mismatch (treated as warm-up, logged). This is the mechanism that makes "feature order must be fixed and versioned" enforceable rather than aspirational.

Extraction reads only the persisted `SignalSnapshot` row (never `IngestionSnapshot`) so live scoring and EOD backfill (TASK-016) share one code path. Accessors flatten deterministically: where a `raw_value` payload is a composite (e.g. `vix_level_and_roc` carries level and rate-of-change), the accessor extracts the named numeric leaves as separate features rather than guessing at one. Missing/None anywhere → `extract_features` returns a vector with that entry `None`; policy for None lives with the callers (trainer drops the row, scorer skips the cycle) — the extractor never imputes. `STALE`/`DISCONNECTED` rows → return `None` outright (a broken feed is not a market anomaly).

Feature set v1 (final names fixed at implementation against `engine._category_raw_readings` output; count ≈ 14):

| Group | Features |
|---|---|
| Sub-scores | `sub_scores["volatility"|"gamma"|"oi_structure"|"order_flow"|"context"]` (5) |
| Composite | `composite_score` (1) |
| Raw readings | `iv_percentile_rank`, VIX level + VIX RoC (from `vix_level_and_roc`), PCR level + PCR RoC (from `pcr_level_and_roc`), GEX magnitude (from `gex_regime`), `spot_distance_from_flip`, `expected_move_consumed_ratio`, CVD divergence measure (from `cvd_direction_and_divergence`) |

`config_type` selects the model downstream; it is NOT in `FEATURE_ORDER`.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/ml/features.py` | `FEATURE_ORDER`, `FEATURE_SET_VERSION`, accessors, `extract_features`, `standardize`, `Scaler` |

## API Contracts

```python
FEATURE_ORDER: tuple[str, ...]
FEATURE_SET_VERSION: int  # starts at 1

class Scaler(BaseModel):
    mean: dict[str, float]   # per feature name
    std: dict[str, float]    # σ==0 in training -> stored as SIGMA_FLOOR (see below)

def extract_features(snapshot: SignalSnapshot) -> dict[str, float | None] | None:
    """None if snapshot.system_status != "OK".
    Else dict keyed exactly by FEATURE_ORDER; individual entries may be None."""

def standardize(raw: dict[str, float], scaler: Scaler) -> list[float]:
    """z_i = (x_i - μ_i) / σ_i, ordered by FEATURE_ORDER.
    Raises KeyError on any missing feature — callers filter Nones first.
    NO fit anywhere in this module."""
```

`SIGMA_FLOOR` (e.g. 1e-9): a feature constant across the training window gets σ floored, yielding z≈0 for in-family values and huge |z| the moment it moves — correct behavior, and it keeps `standardize` total. Guard lives in the trainer at fit time; `standardize` trusts the stored scaler.

## Performance / Failure Modes

Pure dict/tuple work per cycle — negligible. Failure surface is upstream shape drift: if an engine signal renames a `raw_readings` key, accessors return None → scorer skips cycles → visible in `ml_anomaly_scores` as gaps, not crashes. A unit test pins each accessor against a fixture snapshot shaped like real engine output.

## Definition of Done

- [ ] Integration-style tests against the public contracts (no internal mocking)
- [ ] Determinism property test: same snapshot → identical vector (hypothesis)
- [ ] `STALE`/`DISCONNECTED` → None; missing raw key → entry None, others intact
- [ ] `standardize` matches hand-computed z for a known scaler; feature order asserted == FEATURE_ORDER
- [ ] Constraint check: no fitting code in module; no clock reads; config_type absent from FEATURE_ORDER
