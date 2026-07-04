# QA Report — TASK-020

**Date:** 2026-07-04
**Verdict:** ✅ PASS

## Test summary
| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/ml/test_output.py` (HTTP boundary mocked, no DB) | 10 | 10 | 0 | all 4 message types' golden content, distinct-color/prefix assertions, advisory footer presence/absence, warm-up dedup (same day, next day, cross-config independence), unset-webhook no-op, retry/delivery-error behavior |
| `tests/ml/` + `tests/jobs/test_retention_integration.py` regression | 66 | 66 | 0 | TASK-014..019, unaffected |

## Scenarios covered
- **Anomaly advisory:** title carries the `🔬 ML` prefix and the snapshot's `config_type`; description is TASK-019's reason string verbatim (not re-worded); color falls outside the engine's exact palette; footer text is pinned to `"advisory only — does not change engine state"`.
- **Anomaly cleared:** title carries `config_type` (passed explicitly — see debug report's deviation #1) and "Cleared"; description is the clear-reason string verbatim; asserted to have **no** footer key at all (the advisory caveat is anomaly-only).
- **Warm-up progress:** description matches the exact `"day {day} of ~{target}"` template; a second call with the same `(config_type, day)` pair is suppressed (no second POST); a call with an incremented `day` for the same config *is* posted; two different configs at the same `day` both post independently (no cross-config leakage in the guard).
- **Go-live:** title carries `config_type` and "Model Live"; description includes `v{model_version}`.
- **Unset webhook:** constructing `MLDiscordDispatcher(None)` and calling all four post methods raises nothing and performs no HTTP calls (no client/transport exists to even record against).
- **Delivery failure:** 4 consecutive 500s exhausts the fixed retry budget and raises `MLDiscordDeliveryError`, matching `aeolus.output.discord`'s existing retry/backoff shape (reused, not reinvented) verified at exactly the same request count (4).
- **Non-retryable 400:** raises immediately after exactly 1 request, no retry loop entered — same behavior TASK-011's `DiscordDispatcher` already established for this status class.

## Edge cases exercised
- **Webhook not configured:** module disabled, single startup warning log (constructor path), no crash on any subsequent call — the directive's exact wording.
- **Duplicate warm-up line suppression across restarts:** not literally restart-tested (would require killing and reconstructing the process, which the ADR itself already accepts as an out-of-scope tradeoff: "a mid-session restart may repeat one warm-up line"); the in-memory guard's *within-process* behavior is fully tested (same-day suppression, next-day pass-through, per-config independence).
- **Message length truncation:** `_truncate` reuses the same truncate-with-marker approach as `aeolus.output.discord._truncate`; not separately unit-tested here since none of the four message types currently produce content anywhere near the 2000-char Discord description limit (TASK-019's reason strings are short, fixed-shape templates) — low risk, same low-risk judgment call the engine module's own truncate helper already received.

## Gaps / follow-ups
- Two small, documented deviations from the ADR's literal API (added `config_type` param to `post_clear`; `day`-keyed rather than date-keyed warm-up dedup) — both explained in `reports/debug/TASK-020_debug-report.md`, both narrowing rather than widening scope, and both need no further action from TASK-021 beyond passing the now-required arguments.
- `ml_discord_webhook_url` added to `MLTuning` (`src/aeolus/ml/config.py`); the ADR's "falls back to the market webhook when unset" behavior is deliberately **not** implemented inside `MLDiscordDispatcher` itself — the constructor takes a single resolved `webhook_url: str | None` per its own API contract, so the `ml_webhook_url or market_webhook_url` fallback resolution is TASK-021's wiring responsibility when it constructs the dispatcher, not this module's.
- No live-Supabase test needed or written — this task has zero DB interaction by design, consistent with TASK-015/019's precedent for pure-function/no-I/O-beyond-HTTP modules.
