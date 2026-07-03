# Architecture Decision Record — TASK-011

**Directive:** `directives/TASK-011_discord-dispatch.md`
**Status:** APPROVED (2026-07-03)
**Date:** 2026-07-03

## Problem

Format and post Spec §12's three message types — Pre-Market Outlook, state-transition, system-status alert — via Discord webhook. This is the first module whose whole job is presentation: no scoring, no state logic, no new domain computation. It consumes `DailyOutlook` rows (TASK-009), `StateTransition`/`SignalSnapshot` rows (TASK-008), and `template_reason`/`explain_transition` strings (TASK-010), and turns them into two things: a formatted payload and an HTTP POST.

**One genuine design gap surfaced while writing this ADR, flagged for approval below:** the directive requires an "explicit confirm/diverge note" comparing a state-transition against the morning Outlook's `predicted_archetype`. Nothing in the spec defines which archetypes lean GO vs NO-GO — that mapping doesn't exist yet anywhere in the codebase and has to be invented here. See "Archetype → state-lean mapping" below.

## Decision

**One class, `src/aeolus/output/discord.py::DiscordDispatcher`**, constructed with two webhook URLs (`market_webhook_url`, `status_webhook_url` — caller reads these from env, same convention as `SUPABASE_URL`/`SUPABASE_KEY` being passed into `Engine`/`OutlookGenerator` rather than a settings class). Three public methods, one per message type, each a pure format-then-POST — no internal state, no Supabase reads of its own (everything it needs is passed in by the caller, which already has it from TASK-008/009/010).

```python
def post_outlook(self, outlook: DailyOutlook) -> None
def post_transition(self, transition: StateTransition, snapshot: SignalSnapshot, outlook: DailyOutlook | None) -> None
def post_system_status(self, status: SystemStatus, previous_status: SystemStatus) -> None
```

**Two webhooks, not one channel with format-only distinction.** Directive allows "separate channel OR unmistakable format" — using both costs nothing extra (two URLs, two constructor params) and removes any ambiguity about "market is dead" vs "feed is dead" ever being confused, which is the one thing this constraint explicitly says must never happen. `post_system_status` never touches `market_webhook_url`; `post_outlook`/`post_transition` never touch `status_webhook_url`.

**Discord embeds, not plain message content.** Plain-text `content` has a 2000-char limit and no structural fields — a 5-category breakdown with reason strings risks hitting that wall exactly on the message the directive most cares about (state-transition). Embeds give: a color (visual state distinction — green/yellow/red for GO/PREPARE/NO_GO, a distinct grey/black for system-status, never overlapping), a title, and up to 25 separate fields (1024 chars each) — one field per category, holding that category's sub-signal reason strings. If a category's joined reasons exceed 1024 chars (shouldn't happen at 3-4 sub-signals/category and `template_reason`'s pinned 2-decimal formatting, but defensively), truncate with a trailing `"… (truncated)"` marker — visible, not silent.

**Per-category breakdown — reused from existing `SignalSnapshot` structure, no new mapping.** `snapshot.raw_readings` is already keyed `category -> {sub_signal_name: {...}}` (engine.py's existing shape). For each category, iterate its sub-signal keys (skipping the `_carry`/`profile_shape` internal keys engine.py stuffs in there — see engine.py:134-136, :305), look up each sub-signal's reason from `snapshot.reasons[sub_signal_name]` (flat dict, already built by TASK-008). No new category→sub-signal static table needed; this walks data that already exists.

**Archetype → state-lean mapping (the flagged design gap):** to answer confirm/diverge, each `DayArchetype` needs a rough directional lean toward GO/NO_GO/mixed, derived from Spec §4's own "typical premium behavior" column:

| Archetype | Lean | Why (Spec §4's own words) |
|---|---|---|
| `clean_trend` | GO | "delta + vega both work" — unambiguous |
| `grinding_trend` | NO_GO | "premium lags (theta/vega eat delta)" — price moves but premium doesn't, which is exactly what this system exists to flag as NOT favorable for buying |
| `pinned_range` | NO_GO | "dead tape, theta grinds" — unambiguous |
| `choppy_range` | NO_GO | "premium noise without net edge for direction" — matches Section 1's own example of a *failure* pattern for buyers, despite "expanding" vol; GO requires movement a buyer can capture, not noise |
| `breakout_transition` | mixed | spec says "hardest to read live" — explicitly not confidently either way |
| `event_gap` | mixed | spec says "depends on whether IV was already priced in" — explicitly conditional |
| `double_distribution` | NO_GO | "looks trend-y on net change, isn't structurally" — a deceptive/false trend read, same family as grinding_trend |

`mixed`-lean archetypes render as `"not directly comparable to today's Outlook"` rather than forcing a binary confirm/diverge — same "don't fabricate a read the data doesn't support" discipline as `template_reason`'s `None`-raw-value handling. **This table is a judgment call, not derived from anything already agreed — flagging for explicit approval/correction before implementation, same as TASK-009's nudge factors were flagged as unbacktested placeholders.**

Confirm/diverge computed by: `outlook.predicted_archetype`'s lean vs `transition.to_state` (`GO` lean confirms on `to_state=="GO"`, diverges on `"NO_GO"`, `"PREPARE"` is a soft-confirm either direction since it's the ambiguous middle state — rendered as `"partially confirms"`).

**Retry policy (edge case: webhook failure must not drop silently, must not double-post):** bounded retry (3 attempts, exponential backoff: 1s/2s/4s) **only** for cases where non-delivery is a safe assumption — connection/timeout errors before any response is received, and HTTP 429 (honoring Discord's `Retry-After` header, since a 429 response means Discord rejected the request outright). **Never retried:** any response actually received other than 429/5xx (e.g. a 400 means the payload itself is malformed — retrying an identical malformed payload just fails identically, and it's a caller/formatting bug, not a transient condition). 5xx **is** retried (Discord-side failure, safe to assume non-delivery). After exhausting retries, **raise** (`DiscordDeliveryError`) rather than swallowing — directive says "must not drop a transition silently"; a caller (TASK-013 scheduler) that catches and logs is a better failure mode than this module silently eating a failed post. No idempotency key exists on Discord's basic webhook API, so this is the safest achievable middle ground, not a guarantee against all double-post/all-drop scenarios simultaneously (that combination isn't representable without Discord-side dedup support, which doesn't exist).

**Dependency:** `httpx` (already an indirect dependency via `supabase-py`) added as a direct dependency — used for the POST + explicit timeout/retry control, rather than pulling in a second HTTP library (`requests`) or a retry framework for 3 fixed attempts.

**Alternative considered:** a single webhook URL with a `[SYSTEM STATUS]` text prefix as the only distinction. Rejected — directive explicitly treats "requires careful reading to tell apart" as the failure mode to avoid; two channels removes the failure mode structurally instead of relying on a reader noticing a prefix.

**Alternative considered:** dispatcher itself tracks last-posted state to decide whether to post. Rejected — directive explicitly says "this module must not add its own state logic," TASK-008's hysteresis already gates what counts as a genuine transition; `post_transition` is called once per already-decided transition, full stop.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/output/discord.py` | `DiscordDispatcher` — formatting + webhook POST + retry, all three message types |
| `src/aeolus/output/discord.py` | `ARCHETYPE_STATE_LEAN` constant (the flagged mapping above) |
| `pyproject.toml` | `httpx` promoted to a direct dependency |

## API Contracts

```python
class DiscordDispatcher:
    def __init__(self, market_webhook_url: str, status_webhook_url: str) -> None: ...

    def post_outlook(self, outlook: DailyOutlook) -> None:
        """Formats archetype forecast + confidence + contributing_inputs as an
        embed, posts to market_webhook_url. Raises DiscordDeliveryError on
        exhausted retries."""

    def post_transition(
        self, transition: StateTransition, snapshot: SignalSnapshot, outlook: DailyOutlook | None
    ) -> None:
        """Per-category breakdown from snapshot.raw_readings/reasons, confirm/
        diverge note vs outlook.predicted_archetype (None -> no Outlook available
        yet, explicit note, never fabricated). Posts to market_webhook_url."""

    def post_system_status(self, status: SystemStatus, previous_status: SystemStatus) -> None:
        """Terse alert, distinct embed color/title, posts to status_webhook_url only."""


class DiscordDeliveryError(Exception):
    """Raised after retry attempts are exhausted. Caller decides what happens next."""
```

## Performance / Failure Modes

- Webhook POST timeout: 5s connect/read, 3 bounded retries as above (~7s worst case before raising).
- Discord embed field limit (25 fields, 1024 chars/field, 6000 chars total): 5 categories fits trivially; per-field truncation is defensive, not expected to trigger under current sub-signal counts.
- `outlook=None` in `post_transition` (transition fires before the morning Outlook has run, e.g. a same-day restart edge case): confirm/diverge section explicitly states "no Outlook available for today" rather than omitting the section silently or fabricating a comparison.

## Definition of Done

- [ ] Integration-style tests against `DiscordDispatcher`'s public methods (real HTTP calls against a local test server / httpx mock transport — no internal mocking of the class's own methods)
- [ ] System-status message verified structurally distinct (different webhook URL called, different embed color) from market-state messages in the same test run
- [ ] Retry test: simulated 429/5xx/timeout triggers bounded retry; simulated 400 does not retry; exhausted retries raise `DiscordDeliveryError`
- [ ] Archetype confirm/diverge test covering all 7 archetypes × 3 target states, plus `outlook=None`
- [ ] Constraint check: no per-signal veto (n/a), no clock logic (n/a, no clock access here), deterministic reasons (this module renders TASK-010's strings verbatim, adds no free-text beyond the fixed confirm/diverge template), polarity correct (GO=green/favorable framing, NO_GO=red/sit-out framing, never inverted)

## Amendment (2026-07-04): Readability pass, human-directed

Live-tested against the real Discord channels over several rounds (human feedback each round, not a single upfront design). Net result — display-only changes, `template_reason`'s deterministic templated string (constraint #3) is never altered, only re-presented:

1. **Human-readable labels** for categories, sub-signals, and `DayArchetype` values (`_CATEGORY_LABELS`/`_SUB_SIGNAL_LABELS`/`_ARCHETYPE_LABELS` in `discord.py`) replace raw snake_case identifiers (`iv_percentile_rank` -> "IV Percentile Rank", `clean_trend` -> "Clean Trend") everywhere they're displayed, including inside `_confirm_diverge_note`.
2. **Score-only sub-signal lines** — dropped `raw_value`/`reference_band` from the per-sub-signal breakdown (human: "don't show me configs, just the score number"). `_score_line` reads `sub_score` straight from `raw_readings[category][name]`, not by parsing the reason string.
3. **`gex_regime` regime-type annotation** — appends "Short Gamma / Trending" (raw_value < 0) or "Long Gamma / Pinning" (raw_value > 0), derived from the sign already documented in `gamma.gex_regime`'s own docstring. Not a new signal, not a new score — a display label mapped straight off the existing raw_value sign convention.
4. **Fixed a real duplication bug**: `post_transition`'s description was showing `composite=0.67 | ...composite=0.67...` (the embed description prefix duplicated what `explain_transition`'s own reason string already includes). Now shows `transition.reason` directly.
5. **Color-coding, two iterations**: first tried ANSI SGR codes inside ```ansi fenced code blocks (renders as real color on Discord desktop/web) — confirmed via live mobile screenshot that Discord's mobile client does **not** render ANSI, showing raw escape-code text instead. Reverted to 🟢/🟡/🔴 emoji indicators (same favorable/neutral/unfavorable threshold `engine.py` already uses for `trigger_categories`: score >=0.6 / <=0.4 / between) — renders identically on mobile and desktop, confirmed via a second live mobile screenshot.

No new tests needed for the ADR's original Definition of Done criteria (all still hold); `tests/output/test_discord.py` updated in place for the new display format (12 tests, +1 for the `gex_regime` "no data" fallback path via `raw_value=None` rather than a canned reason string).
