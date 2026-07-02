# AEOLUS

**NIFTY Regime & Premium-Movement Forecasting System**

> A weather app for the market, not a signal generator. It doesn't tell you when to enter — it tells you what kind of day it is, and what kind of day it is becoming, so option-buying entries aren't made into conditions where theta wins regardless of direction being right.

## What it does

- **Pre-Market Outlook** (once per session, pre-open): probabilistic day-archetype forecast posted to Discord, led by the prior-day trend-exhaustion check.
- **Live State** (continuous, 9:15–15:30 IST): NO-GO / PREPARE / GO composite regime read, posted to Discord only on debounced state transitions.

**Polarity:** GO = favorable for directional option *buying*. Inverse of premium-selling tools. See `docs/CONSTRAINTS.md`.

## Stack

Python 3.11+ · Dhan API v2 (live feed) · Supabase/Postgres (storage) · Discord webhooks (output)

## Links
Dhan HQ API V2 - https://docs.dhanhq.co/api/v2/

## Documentation map

| Doc | Purpose |
|---|---|
| `files/AEOLUS_SYSTEM_SPEC.md` | Canonical system specification (v1.0) |
| `files/AEOLUS_BUILD_PROMPTS.md` | Module-by-module build prompts, dependency order |
| `docs/ARCHITECTURE.md` | Component map, data flow, module boundaries |
| `docs/CONSTRAINTS.md` | Hard build constraints (veto ban, no clock logic, polarity) |
| `docs/DATA_MODEL.md` | Supabase schema (4 tables) |
| `docs/OPEN_DECISIONS.md` | Unresolved decisions — confirm before affected builds |
| `directives/` | PM directives TASK-001..013 |
| `directives/adr/` | ADRs (written per-task before implementation) |
| `reports/debug/`, `reports/qa/` | Per-task verification reports |
| `CHANGELOG.md` | Version history |

## Status

Scaffolded — no code yet. Next step: resolve `docs/OPEN_DECISIONS.md`, then TASK-001 (Supabase schema) via ADR.
