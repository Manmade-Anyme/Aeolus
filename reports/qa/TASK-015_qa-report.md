# QA Report — TASK-015

**Date:** 2026-07-04
**Verdict:** ✅ PASS

## Test summary
| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/ml/test_features.py` | 12 | 12 | 0 | Extraction values, key-order contract, `STALE`/`DISCONNECTED` gating, missing-leaf isolation, determinism (fixed + Hypothesis property), `standardize` correctness + `KeyError`, `config_type` exclusion, version constant |
| `tests/ml/` (regression, incl. TASK-014 models) | 19 | 19 | 0 | — |

## Scenarios covered
- **Value extraction:** every `FEATURE_ORDER` entry pulled from a fixture shaped like real `engine.py` output — 5 category sub-scores, composite, and the 9 raw-reading leaves (including the two composite level+RoC pairs read from a sub-signal's `context` dict, e.g. `vix_level_and_roc`'s level from `raw_value` and RoC from `context["roc"]`; `pcr_level_and_roc`'s RoC from `raw_value` and level from `context["pcr_level"]` — the ADR's flattening rule for nested payloads).
- **Key-order contract:** `list(vector.keys()) == list(FEATURE_ORDER)`, generated from the same single `_ACCESSORS` mapping so the two can't drift apart.
- **Broken feed:** both `STALE` and `DISCONNECTED` → `None` (parametrized).
- **Partial data:** a missing `context` leaf (`vix_roc`/`pcr_level`) or a missing `raw_value` (`gex_regime` → `gex_magnitude`) yields `None` for that entry only — sibling features in the same category are verified unaffected.
- **Determinism:** one fixed-snapshot check plus a Hypothesis property test across `vix_roc`/`pcr_level`/`gex_raw` combinations (including `None`) — same snapshot always extracts to an identical dict.
- **`standardize`:** hand-computed z-scores for a uniform mean/std scaler; `KeyError` when the raw dict is missing a feature the scaler expects (contract: callers must filter `None`s first, this module doesn't tolerate gaps silently).

## Edge cases exercised
- `_carry` sibling key present in every category dict (real engine behavior) — confirmed accessors ignore it, only ever looking up the named sub-signal key.
- `gex_regime`'s signed `raw_value` → `gex_magnitude` is `abs()`, verified both on a present value and the `None`-propagation case.

## Gaps / follow-ups
- No live-data test (this module has no I/O — nothing to verify live; a fixture built from the actual engine assembly code is the correct verification method here, not a live Supabase round-trip).
- The exact 9 raw-reading feature choices are frozen at v1 per the ADR; any future addition/removal bumps `FEATURE_SET_VERSION` and is TASK-017/018's concern to detect via the mismatch-refusal rule — out of scope for this task.
