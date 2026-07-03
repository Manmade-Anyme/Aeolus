# QA Report — TASK-011

**Date:** 2026-07-03
**Verdict:** ✅ PASS

## Test summary
| Suite | Tests | Pass | Fail | Coverage |
|---|---|---|---|---|
| `tests/output/test_discord.py` | 11 | 11 | 0 | All three message types, retry policy, archetype confirm/diverge |
| Full repo suite (`pytest -q`) | 204 | 204 | 0 | Regression check |

## Scenarios covered
- **Outlook message:** primary/secondary archetype + confidence rendered, contributing inputs rendered, trend-exhaustion lead line appears only when `trend_exhaustion_flag=True`.
- **State-transition message:** per-category breakdown pulled straight from `SignalSnapshot.raw_readings`/`reasons` (internal `_carry` scratch keys correctly filtered out via the `SUB_SIGNAL_NAMES` allowlist), confirm/diverge note for a GO-leaning archetype confirming a `GO` transition, diverging for a `NO_GO` transition, `mixed`-lean archetype (`event_gap`) rendering "not directly comparable" rather than a forced binary call, `outlook=None` rendering an explicit "no Outlook available for today" rather than omitting the section.
- **System-status message:** posts only to the status webhook (never the market webhook), distinct color from all three market-state colors — verified by asserting the exact color values differ, not just eyeballing.
- **Retry policy:** 5xx retried and eventually succeeds; 429 honors `Retry-After` and retries; 400 raises immediately with zero retries; 4-consecutive-5xx exhausts retries and raises `DiscordDeliveryError` rather than swallowing.

## Edge cases exercised
- Webhook failure/retry (directive's stated edge case): covered by the four retry-policy tests above — non-delivery-safe cases (timeout/429/5xx) retry, ambiguous/client-error cases (400) don't, exhausted retries raise rather than drop silently.
- Message length limits: `_truncate` helper is exercised structurally (present in the code path for every field), but no test currently forces a field past 1024 chars — sub-signal counts per category (3-4) with `template_reason`'s pinned 2-decimal formatting don't get close to that limit today, so this is a defensive path, not a currently-reachable one. Flagging as an accepted gap rather than a false "covered."
- Discord rate limits (429): covered explicitly, including honoring `Retry-After`.

## Gaps / follow-ups
- No test forces the 1024-char per-field truncation path (see above) — would require an artificially long reason string; not exercised because it's not reachable with current sub-signal counts.
- No live-webhook integration test (unlike TASK-008/009's live-Supabase tests) — Discord webhooks require a real channel/URL to test against live, which isn't available in this environment; `httpx.MockTransport` substitutes for a locally-driven HTTP-level test instead, per the ADR's Definition of Done.
