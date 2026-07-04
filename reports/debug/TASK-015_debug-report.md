# Debug Report — TASK-015

**Date:** 2026-07-04
**Verdict:** ✅ CLEAN

## What was run
- `pytest tests/ml/test_features.py -q` — 12 tests, including 2 Hypothesis-driven determinism checks.
- `pytest tests/ml/ -q` (regression) — 19/19 (7 from TASK-014 + 12 new).
- `ruff check` + `mypy` on `src/aeolus/ml/features.py`, `tests/ml/test_features.py` — clean.

## Observed behavior
`19 passed`. Verified the fixture's `raw_readings` shape against the real assembly logic in `engine.py:412-424` (`_category_raw_readings`) and the per-category call sites (`engine.py:137,163,203,252,307`) rather than inventing a plausible-looking shape — confirmed sub-signal entries are `{raw_value, reference_band, sub_score, context?}` keyed by sub-signal name under each category, with an additional `_carry` sibling key the extractor must silently ignore (it does, since accessors only ever look up named sub-signal keys).

## Constraint audit
- [x] No per-signal veto present — n/a, pure extraction, no scoring/gating
- [x] No clock-time branching in signal logic — grep confirms no `datetime.now`/`date.today` calls anywhere in `features.py`
- [x] Reason strings deterministic — n/a, no reason strings produced here
- [x] Polarity check: GO favors option buying — n/a
- [x] `system_status` never feeds `market_state` — inverted concern here: `system_status` correctly *gates extraction itself* (`STALE`/`DISCONNECTED` → `None`), never touches or reads `market_state`
- [x] No fitting code anywhere in `features.py` (`grep -n "fit\|Scaler(" src/aeolus/ml/features.py` — only the `Scaler` pydantic model definition and its consumption in `standardize`, no `.fit(`)
- [x] `config_type` absent from `FEATURE_ORDER` (explicit test)
