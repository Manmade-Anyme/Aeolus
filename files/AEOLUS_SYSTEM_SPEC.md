# AEOLUS — NIFTY Regime & Premium-Movement Forecasting System
**System Specification v1.0**
*Last updated: 2 July 2026*

> AEOLUS is a weather app for the market, not a signal generator. It does not tell you
> when to enter. It tells you what kind of day it is, and what kind of day it is
> becoming, so option-buying entries are not made into conditions where theta will
> win regardless of direction being right.

---

## 1. Purpose

Prevent NIFTY option-buying entries during regimes where premiums structurally
cannot move enough to overcome theta decay — even when directional read is correct.

Three patterns this system exists to catch:
- Range-bound but volatile (IV/gamma noise without net displacement) — premiums
  whip but a directional buyer gets chopped both ways.
- Trending but flat (price moves, IV compresses or was already rich) — delta gains
  get eaten by vega loss and theta.
- **Post-trend digestion** — a clean trend day is consistently the profitable
  case; the session immediately following one is where losses concentrate,
  because momentum fades faster than it feels like it should. This is the
  specific, real pattern that motivated building AEOLUS, and it is why the
  Pre-Market Outlook (Section 5.1) treats yesterday's completed profile as a
  headline input rather than one signal among many.

## 2. Scope & Boundaries

| In scope | Out of scope |
|---|---|
| NIFTY 50 index options only | Bank Nifty, stock options, other indices |
| Dhan API v2 (live feed, option chain, OI, greeks, depth) | Any other broker/data API |
| Current-month NIFTY futures LTP (Dhan API v2) | Synthetic future derived via put-call parity |
| GIFT Nifty for overnight/pre-market cue | SGX, Dow, Asian markets, any other overnight cue |
| Supabase (Postgres) for storage | — |
| Discord (webhook) for output | Signal/entry generation, position sizing, execution |

## 3. Core Philosophy: Two Independent Axes

Every trading day is a combination of two separate variables that must be tracked
independently:

1. **Directional character** — trending vs range-bound
2. **Volatility character** — IV expanding/supportive vs IV contracting/compressing

Premium movement is a function of both, not either alone. A trend day with
compressing IV can be as dead for premiums as a quiet range day. A range day with
expanding/whipping IV can look "loud" while still being a losing environment for a
directional buyer.

## 4. Day-Type Taxonomy (reference)

| # | Archetype | Directional | Volatility | Typical premium behavior |
|---|---|---|---|---|
| 1 | Clean/breakout trend | Trend | Expanding | Best case — delta + vega both work |
| 2 | Grinding/orderly trend | Trend | Contracting/flat | Price moves, premium lags (theta/vega eat delta) |
| 3 | Quiet/pinned range | Range | Contracting | Dead tape, theta grinds |
| 4 | Choppy/volatile range | Range | Expanding/whipping | Premium noise without net edge for direction |
| 5 | Breakout/transition | Range→Trend | Shifting | Regime changes mid-session, hardest to read live |
| 6 | Event/gap | Either | Spike then crush, or expand together | Depends on whether IV was already priced in |
| 7 | Double-distribution/rotational | Two mini-ranges | Mixed | Looks trend-y on net change, isn't structurally |

## 5. Output Model — Two Distinct Reports

### 5.1 Pre-Market Outlook (single run, once per session)

**Headline driver — Prior-Day Trend Exhaustion Check:** this is the specific
pattern the project exists to catch (Section 1), so it is surfaced as its own
explicit line in the Outlook, not blended silently into a probability score.
If yesterday's completed profile resolved as a clean/elongated trend day, the
Outlook must state this plainly and raise the prior for today resolving into
digestion/consolidation (Archetypes 2/3 in Section 4) — ahead of any other
input being weighed.

**Other inputs:**
- GIFT Nifty gap vs prior NIFTY close
- Yesterday's completed volume-profile shape (balanced vs trend day — feeds
  the headline driver above)
- ATM straddle premium **level** vs its own recent history (10–20 session
  lookback) — is today's opening premium pricing in an unusually large or
  small move compared to recent sessions. Distinct from the live
  expected-move-consumed ratio in Section 6.1, which needs realized intraday
  move and so cannot run pre-market.
- IV percentile heading in, VIX level/trend
- OI and max-pain structure carried over from prior close
- Current-month NIFTY futures price context (Section 2)
- DTE/expiry flag

**Note on volume:** there is no live "today" volume before the open. Volume
input here is limited to yesterday's completed profile and overnight OI
rollover. Today's actual volume only becomes usable once the Live State
engine takes over after 9:15 — it is not part of the Outlook.

**Output:** a probabilistic read across the Section 4 archetypes (e.g. "60% grinding
trend, 25% pinned range, 15% breakout") plus a primary/secondary call and a
confidence measure, led by the trend-exhaustion read where applicable. This is
a forecast prior, not a state — it does not use the NO-GO/PREPARE/GO
vocabulary.

**Delivery:** posted to Discord once, pre-open.

### 5.2 Live State (continuous, event-driven)

Three states only: **NO-GO**, **PREPARE**, **GO**.

> **Polarity — read before building.** AEOLUS is built for a directional option
> **buyer**, not a premium seller. **GO** means conditions favor buying —
> movement/trend developing, IV with room to expand, enough realized move
> likely to clear theta. **NO-GO** means quiet/pinned, sit out. This is the
> *inverse* of what premium-selling regime tools use the same words for
> (where GO = low volatility = safe to sell). Do not copy polarity conventions
> from any reference tool without checking this note.

- Computed as a weighted composite score across the five signal categories
  (Section 6). No category and no individual sub-signal has unilateral veto power.
  **NO-GO must always emerge from the composite score landing low — never from a
  hardcoded rule on any single signal.** This is a hard constraint on the design.
- Posts to Discord only on genuine state transitions (debounced — Section 7), and
  each post references whether the live read is confirming or diverging from the
  morning Outlook.
- A separate **system status flag** (`OK` / `STALE` / `DISCONNECTED`) sits outside
  the three market states. Feed dropouts or halted data are a data-integrity
  problem, not a market read, and must never be allowed to masquerade as a
  computed NO-GO.

## 6. Signal Categories

Five categories, shared across both configs (Section 8) — only weights and
thresholds differ, not the category structure itself.

### 6.1 Volatility
- IV percentile/rank vs trailing 20–60 day range
- IV vs realized-vol spread
- India VIX level + rate of change
- **ATM straddle expected-move-consumed ratio** — realized move so far ÷
  straddle-implied expected move for the session. Highest-value single signal in
  this category; directly answers "is the day delivering on what premium priced in."
  Live-only — needs realized intraday move, so it cannot run pre-market. The
  pre-market equivalent (straddle premium level vs its own recent history) lives
  in the Pre-Market Outlook, Section 5.1.

### 6.2 Gamma
- GEX / zero-gamma flip level: sign (dealer positioning regime) **and** magnitude
  (conviction — weak negative gamma won't amplify much, strong negative gamma will)
- Spot's distance from the flip level

### 6.3 OI Structure
- PCR level **and** rate of change (a static PCR of 1.1 tells you less than one that
  moved from 0.9→1.2 in an hour)
- OI buildup classification per strike, from joint price+OI read: long buildup,
  short covering, short buildup, long unwinding
- OI wall proximity on both sides, and wall strength/decay through the session
- Max pain drift over the session

### 6.4 Order Flow
- CVD build direction and divergence from price (price progress without CVD
  confirmation = fragile move)
- Delta imbalance / absorption at range extremes
- **Session-relative volume-participation range** in place of a clock-based
  "opening range": the range formed by the first X% of the day's cumulative volume,
  rather than the first N minutes. This preserves the structural concept (an
  early reference range that price either holds inside or breaks and holds
  outside of) without hardcoding any clock time — see Section 14, Open Decision 1.

### 6.5 Context
- Yesterday's completed profile shape (balanced/rotational vs trend day) — feeds
  the Pre-Market Outlook primarily
- Gap type at open (gap-and-go vs gap-and-fill) vs yesterday's value area
- DTE relative to the NSE weekly/monthly expiry (Tuesday-anchored, holiday-shift
  aware — see Section 8)
- No clock-based intraday interpretation rules anywhere in this category, or
  anywhere in the system.

## 7. Composite Scoring & State Machine

- Composite score = weighted sum of the five category sub-scores. Weights are set
  per config (Section 8).
- Score-to-state thresholds (what composite value separates NO-GO / PREPARE / GO)
  are calibration knobs, not market-condition rules. Start from judgment, then
  refine empirically once the labeled dataset (Section 10) has enough history to
  check whether a given cutoff actually correlates with favorable forward moves.
- **Hysteresis/debounce is mandatory.** A state change must hold for a
  confirmation window (N consecutive computation cycles, or the composite must
  cross by a defined margin) before it flips and before anything is posted to
  Discord. Without this, states near a boundary will flap constantly, which
  defeats the "weather forecast" premise.
- The engine runs continuously across the full session (9:15–3:30 IST) with
  **no internal behavior change tied to clock time** — this satisfies both "works
  the entire day" and "no hardcoded time-based conditions" simultaneously: the
  scheduler decides *when* to run, the signals never decide *how to interpret*
  based on *when* it is.

## 8. Dual Configuration: Expiry Day vs Non-Expiry Day

Nifty weekly and monthly expiry falls on **Tuesday** (NSE revised this from
Thursday effective 1 September 2025; if Tuesday is a trading holiday, expiry
shifts to the previous trading day — check against the NSE holiday calendar,
don't hardcode the weekday).

Same five categories, same signal formulas, **different weight/threshold tables**:

- **Gamma/OI walls:** weighted higher on expiry day — pinning intensifies as
  dealers actively defend short strikes into the close; walls should be treated
  as stickier/harder to break.
- **Volatility:** IV-percentile bands recalibrated lower for expiry day — IV
  structurally bleeds out through the session as the week's uncertainty resolves.
  This is expected behavior on expiry day, not a compression warning the way it
  would be on a non-expiry day.
- **Overall GO bar:** raised on expiry day, particularly in the final hours,
  because theta severity means even a real directional move may not produce a
  worthwhile premium gain.

Config selection is driven by DTE (days to expiry), computed from the Tuesday
anchor with holiday-shift awareness. See Section 14, Open Decision 2 for a
possible v2 refinement (DTE-graduated weighting instead of a strict binary).

## 9. Supabase Schema (conceptual)

Live data and backtest data are the same append-only log — no separate synthetic
backtest table.

- **`signal_snapshots`** — one row per computation cycle: raw readings, sub-scores
  per category, composite score, state, system status, reason strings, config
  type used, DTE, day context. This table is the ML feature set by construction.
- **`state_transitions`** — thin log, only rows where the state actually changed:
  entry/exit state, trigger category, reason, timestamp. This is what gets posted
  to Discord.
- **`daily_outlook`** — one row per trading day: the pre-market forecast
  (predicted archetype, confidence, contributing inputs — explicitly including
  the prior-day trend-exhaustion flag and straddle-level-vs-history reading),
  backfilled after close with the actual realized archetype. The labeled
  dataset for the pre-market forecasting model specifically.
- **`outcome_labels`** — backfilled after the fact (not computed live, since the
  label isn't known at signal-time): for snapshots/transitions, the forward
  realized outcome — straddle price change, realized move, direction — at
  +15/+30/+60 minutes. A separate enrichment job.

**Known limitation:** this schema accumulates data from go-live forward only.
Backtesting against dates before the system existed needs a separate historical
data source (NSE bhavcopy or a paid vendor) — Dhan's live feed doesn't hand you
historical tick-level OI/depth for past dates. Out of scope unless explicitly
pursued.

## 10. Explainability

Every signal category emits, at every computation cycle:
1. Current raw reading
2. Reference band/threshold it's compared against
3. Sub-score contribution to the composite
4. A **templated, deterministic** reason string built directly from (1)–(3)

Reason generation must not be LLM-narrated. The reason has to trace back to a
specific number crossing a specific threshold, every time, so it stays fully
backtestable and never invents a rationale untethered from the data.

State-transition messages must cite the category/categories whose sub-score
movement actually drove the transition.

## 11. Backtesting / ML Loop

- `outcome_labels` enrichment job runs after the fact, joining forward market
  data back onto historical snapshots/transitions.
- This produces two independent, scoreable datasets over time:
  1. Live-state accuracy — did GO calls precede real premium movement, did
     NO-GO calls precede genuinely quiet stretches
  2. Pre-market Outlook accuracy — did the forecast archetype match the realized
     day-type
- These datasets are what eventually let composite weights and state thresholds
  move from judgment-calibrated to empirically-calibrated.

## 12. Discord Output

- **Pre-Market Outlook message:** day-archetype forecast, confidence, key
  contributing inputs.
- **State-transition message:** new state, composite score, per-category
  current-condition breakdown with reasons, explicit note on whether this
  confirms or diverges from the morning Outlook.
- **System-status message:** separate, terse alert if status flips to
  `STALE`/`DISCONNECTED` — distinct channel or clearly distinguishable format
  from market-state messages.

## 13. Explicit Non-Goals

- Not an entry/exit signal generator — direction hints in Section 5.1 are a
  forecast, not a trade call.
- Not multi-instrument.
- Not backtestable against pre-launch history without a separate data source.
- No hardcoded per-signal veto logic.
- No hardcoded intraday clock-time interpretation rules anywhere.

## 14. Open Decisions (confirm before build)

1. **Volume-participation range** (Section 6.4) is my reframe of "opening range"
   to stay compliant with the no-time-based-logic rule. Confirm you want this
   included, or want it dropped entirely, or want it defined differently.
2. **DTE-graduated weighting** (Section 8) — v1 as specified uses a strict binary
   expiry/non-expiry config. A continuous DTE-based weighting (Monday behaves
   closer to Tuesday than Wednesday does) is a natural v2 — confirm if you want
   it in v1 instead.
3. **Historical backfill** (Section 9) — confirm whether backtesting against
   pre-launch dates is a hard requirement (which adds a historical-data-sourcing
   workstream) or whether building the labeled dataset live from go-live forward
   is acceptable for v1.
4. **Futures basis** (futures − spot, and its drift through the session) — now
   that a direct futures feed is a required data source (Section 2), this
   becomes cheap to add as an optional secondary positioning/sentiment signal.
   Not required for v1; confirm if you want it included now or left for v2.
