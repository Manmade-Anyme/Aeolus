# Architecture Decision Record — TASK-018

**Directive:** `directives/TASK-018_ml-live-scorer.md`
**Status:** APPROVED — planning merged via [PR #17](https://github.com/dubeyshantanu2/Aeolus/pull/17) (commit `909de7d`), 2026-07-04
**Date:** 2026-07-04

## Problem

Per cycle: score the current snapshot's vector against the latest model for its config_type, persist every score, and run the debounced + hysteresis-gated anomaly state machine so a normal day posts nothing and an anomalous stretch posts exactly once on entry (optionally once on exit).

## Decision

`LiveScorer` holds (a) a per-config model cache and (b) a per-config `AnomalyState`. The cache stores the deserialized model + scaler + thresholds keyed by registry row id; it re-queries "latest version for config_type" lazily with a short TTL (config, default 300s) rather than per cycle — a retrain lands overnight, so intra-session version flips are rare, and TTL keeps the scorer self-healing without a registry read per cycle. Version-compat guards from TASK-014/015 apply at load: sklearn major.minor mismatch or `feature_set_version` mismatch → treat as no-model (WARMING_UP), log loudly.

Scoring path mirrors the spec §7 order exactly: refuse `STALE`/`DISCONNECTED` (return None, no row — a broken feed is not a market anomaly); extract; any None feature → skip cycle (no row, logged counter); no active model → write score row? No — with no model there is no score; write `MLAnomalyScore` with `ml_status="WARMING_UP"`, `score` from the model *only if one exists*. Concretely: **no model → no score row is impossible to fill honestly, so the row is written only when a model scored** (`ACTIVE` or a warming-up config that has a silent candidate model is v2 — v1 keeps it simple: no model, no row; warm-up *progress* is TASK-020's daily line, driven by trainer state, not per-cycle rows).

State machine (per config, session-scoped, reset by `start_session`): `NORMAL → ANOMALOUS` only when `score >= flag_threshold` (debounce = transition-edge posting; the `on_enter` event fires once); `ANOMALOUS → NORMAL` only when `score < clear_threshold`; between thresholds → hold state, no events. Optional `min_dwell_cycles` (config, default 0 = off) blocks any flip until the current state has held N cycles. Events are returned to the caller (TASK-021 hook), which invokes explanation (TASK-019) and posting (TASK-020) — the scorer never talks to Discord itself.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/ml/scorer.py` | `LiveScorer`, `AnomalyState`, model cache, hysteresis/debounce |

## API Contracts

```python
class ScoreEvent(BaseModel):
    kind: Literal["ANOMALY_ENTER", "ANOMALY_CLEAR"]
    score: float
    z_by_feature: dict[str, float]     # standardized vector, TASK-019's input
    model_version_id: UUID

class LiveScorer:
    def __init__(self, supabase_url: str, supabase_key: str,
                 *, tuning: MLTuning | None = None, client: Client | None = None): ...

    def start_session(self, session_date: date) -> None: ...

    def score_cycle(self, snapshot: SignalSnapshot) -> ScoreEvent | None:
        """Full live path: refuse/extract/standardize/score/persist/step state
        machine. Returns an event ONLY on a state transition; None otherwise
        (including every cycle of an ongoing anomaly — debounce lives here).
        Never raises: internal errors are caught, logged, -> None."""
```

Persisted row per scored cycle: score, `flagged` (current state == ANOMALOUS), `ml_status="ACTIVE"`, top_features (populated on transitions only), model_version_id.

## Performance / Failure Modes

One IsolationForest `.score_samples` on a 14-dim vector — microseconds. One insert per cycle. Registry unreachable at TTL refresh → keep the cached model, log; unreachable with an empty cache → behave as warming up. Score exactly at flag threshold: `>=` enters (documented in code); exactly at clear: `<` exits — boundary behavior pinned by tests. Model version flip mid-session: state carries over, thresholds switch to the new model's pair (documented; acceptable because retrains land post-close).

## Definition of Done

- [ ] Integration-style tests with a real fitted toy model (no mocking of internals): normal stream → zero events; spike → exactly one ANOMALY_ENTER; hover between thresholds → no flapping; drop below clear → one ANOMALY_CLEAR
- [ ] STALE/DISCONNECTED and missing-feature cycles produce no rows, no events, no exceptions
- [ ] min_dwell honored when enabled; default off
- [ ] feature_set_version / sklearn_version mismatch → WARMING_UP behavior, loud log
- [ ] Constraint check: never writes engine tables; never reads the clock; flag decided by IF score vs threshold only — z-scores explain, never decide
