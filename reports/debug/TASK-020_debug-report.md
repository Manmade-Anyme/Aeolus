# Debug Report — TASK-020

**Date:** 2026-07-04
**Verdict:** ✅ CLEAN

## What was run
- `pytest tests/ml/test_output.py -q` — 10 tests, HTTP boundary mocked via `httpx.MockTransport` (same convention as `tests/output/test_discord.py`, no internal mocking).
- `pytest tests/ml/ tests/jobs/test_retention_integration.py -q` (regression) — 66/66 (56 prior + 10 new).
- `pytest tests/ -q` (full repo) — 298 passed, 1 failed (pre-existing, unrelated live-Dhan-API failure in `test_ingestion_service_end_to_end`).
- `ruff check` on `src/aeolus/ml/output.py`, `src/aeolus/ml/config.py`, `tests/ml/test_output.py` — clean.
- `mypy src/aeolus/ml/` — clean (9 source files).

## Observed behavior
All 10 new tests pass on first correct implementation. One pre-existing tooling quirk reproduced but not caused by this task: running `mypy` against `tests/ml/test_output.py` directly (or `tests/output/test_discord.py` directly, checked for comparison) reports spurious `**dict[str, object]` argument-type errors against the pydantic model constructors in the test fixtures' `_snapshot()`/`_event()` helpers. This is the *same* `**dict(...)` fixture-building pattern used by every prior test file in this repo (`tests/output/test_discord.py` reproduces the identical error class when checked the same way) — confirmed pre-existing and environment-wide (likely a stricter mypy version than when TASK-011 was validated), not a regression. Production code (`src/aeolus/ml/output.py`, `config.py`) has zero mypy errors on its own.

## Issues
| # | Severity | Description | File:Line | Status |
|---|---|---|---|---|
| — | — | None found in production code | — | — |
| 1 | Pre-existing tooling quirk (not a regression) | `mypy` on a single test file reports `**dict` invariance errors against pydantic constructors in `_snapshot()`/`_event()`-style fixture helpers; reproduces identically in the already-merged `tests/output/test_discord.py` | `tests/ml/test_output.py`, `tests/output/test_discord.py` | Not fixed (pre-existing, out of scope) |

## Constraint audit
- [x] No per-signal veto present — n/a, this module only formats/posts; whether to post is decided entirely by TASK-018 (debounce/hysteresis) and TASK-021 (the hook), never here
- [x] No clock-time branching in signal logic — the warm-up dedup guard uses the caller-supplied `day` progress counter (already an int, incremented once per session by TASK-021) as its key, not a `date.today()`/`datetime.now()` read; grep confirms no wall-clock calls anywhere in `output.py`
- [x] Reason strings deterministic — n/a here; the reason *strings* passed into `post_anomaly`/`post_clear` are TASK-019's templated output, this module only lays them into an embed unmodified (`_truncate` only, no re-wording)
- [x] Polarity check: GO favors option buying — n/a, advisory ML overlay
- [x] `system_status` never feeds `market_state` — n/a, this module never reads `system_status` or writes any table
- [x] Never writes engine tables — grep confirms `output.py` has no `supabase`/table references at all; it is a pure HTTP-posting class
- [x] Visually unmistakable vs engine messages — `_TITLE_PREFIX = "🔬 ML"` on every title; all four colors (`_ANOMALY_COLOR`, `_CLEAR_COLOR`, `_WARMUP_COLOR`, `_GOLIVE_COLOR`) asserted in tests to be outside `aeolus.output.discord`'s exact palette (`{0x2ECC71, 0xF1C40F, 0xE74C3C, 0x9B59B6}`); advisory footer text pinned by golden-string test
- [x] Advisory footer present only where mandated — `test_post_clear_is_single_line_no_advisory_footer` asserts `"footer" not in embed` for the clear message (footer is anomaly-only per the ADR, since only the anomaly advisory itself needs the "does not change engine state" caveat)

## Design deviations from the ADR (both minimal, both documented for TASK-021's integrator)
1. **`post_clear` gained a `config_type: ConfigType` parameter** not present in the ADR's literal signature (`post_clear(self, event: ScoreEvent, reason: str) -> None`). Reason: the ADR's own "Message content" list requires `config_type` on every message, but neither `ScoreEvent` nor `clear_reason`'s output carries it — omitting it would make a cleared-anomaly message ambiguous between the two independently-scored configs (EXPIRY/NON_EXPIRY). Added the parameter rather than modifying `ScoreEvent` (a TASK-018 model) or `clear_reason` (a TASK-019 template) — the caller already has `config_type` on hand from the snapshot it just scored.
2. **`post_warmup_progress`'s once-per-day guard keys on the caller-supplied `day` int, not a wall-clock date.** The ADR describes "an in-memory last-posted date per config" as the resolution but doesn't specify where the date comes from. Since `day` already increases by exactly one per calendar day by construction (upstream, TASK-021's job), using it directly as the dedup key achieves the identical guarantee with zero wall-clock reads in this module — a strictly cleaner realization of the same idea, and keeps `output.py` fully consistent with this repo's "no clock reads outside the scheduler" spirit.
