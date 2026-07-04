# Architecture Decision Record — TASK-020

**Directive:** `directives/TASK-020_ml-discord-output.md`
**Status:** APPROVED — planning merged via [PR #17](https://github.com/dubeyshantanu2/Aeolus/pull/17) (commit `909de7d`), 2026-07-04
**Date:** 2026-07-04

## Problem

Post four ML message types to Discord — anomaly advisory (entry), anomaly cleared (exit), daily warm-up progress, warm-up go-live — visually unmistakable vs engine messages, with cadence guarantees (advisory/clear only on TASK-018 transitions; warm-up lines max once per day per config).

## Decision

`MLDiscordDispatcher` mirrors `DiscordDispatcher`'s shape (httpx POST to a webhook, embeds, truncation, IST presentation for timestamps) but is a separate class in `aeolus.ml` — engine output code stays ML-free. It posts to `ML_DISCORD_WEBHOOK_URL` (new env var via `MLTuning`); if unset it falls back to the market webhook, because a separate channel is nice-to-have while the distinct format is the actual safety guarantee. Every message: `🔬 ML` title prefix, distinct embed color, and the advisory carries the mandatory footer *"advisory only — does not change engine state"*.

Whether to post is never decided here: advisory/clear fire only when the TASK-021 hook hands over a `ScoreEvent`; warm-up lines fire from EOD/first-cycle trainer state. Once-per-day suppression for warm-up progress must survive process restarts (the Fly.io machine starts fresh daily but a mid-session restart must not re-post), so the guard is a lookup, per config, for an existing progress post *today* — persisted via a small `posted_markers` jsonb… rejected: new table for one guard is overkill. Instead the guard queries `ml_anomaly_scores` for today's rows? Also wrong-shaped (WARMING_UP configs write no rows per TASK-018). Final: in-memory last-posted date per config + acceptance that a mid-session restart may repeat one warm-up line — explicitly traded against schema surface; documented in code. Go-live notice: posted when the hook observes a config's trainer result transition WARMING_UP→TRAINED (first version row for that config), which is derivable from the registry (`version == 1`) — restart-safe with no new state.

Delivery failures raise the module's `MLDiscordDeliveryError`; the TASK-021 hook catches and logs — a Discord outage never touches the engine loop, matching the scheduler's existing pattern for engine messages.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/ml/output.py` | `MLDiscordDispatcher`, message builders, `MLDiscordDeliveryError` |

## API Contracts

```python
class MLDiscordDispatcher:
    def __init__(self, webhook_url: str | None): ...   # None -> disabled, log once

    def post_anomaly(self, event: ScoreEvent, reason: str, snapshot: SignalSnapshot) -> None: ...
    def post_clear(self, event: ScoreEvent, reason: str) -> None: ...
    def post_warmup_progress(self, config_type: ConfigType, day: int, target: int) -> None:
        """Max once per day per config (in-memory date guard)."""
    def post_golive(self, config_type: ConfigType, model_version: int) -> None: ...
```

Message content: score, top dimensions + z-scores, model version, config_type, footer. All numbers pre-formatted by TASK-019's templates — this module lays out, it never re-words.

## Performance / Failure Modes

One webhook POST per event — events are rare by construction (TASK-018 guarantees). Webhook unset → all posts no-op after a single startup log. Truncation guard reused from the engine formatter's approach (Discord 2000-char/embed limits).

## Definition of Done

- [ ] Integration-style tests via injected transport (respx/httpx mock at the HTTP boundary, not internal mocks): payload golden tests for all four message types — prefix, color, footer present
- [ ] Warm-up progress: second call same day same config → no POST
- [ ] Unset webhook → no POST, no crash
- [ ] Delivery failure → MLDiscordDeliveryError raised, nothing else
- [ ] Constraint check: no posting decisions made here; format cannot be confused with engine transition or system-status messages (distinct prefix asserted in tests)
