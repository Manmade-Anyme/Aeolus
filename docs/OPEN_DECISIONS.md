# AEOLUS — Open Decisions

From Spec §14. **Each must be resolved with the human before the affected module's ADR is approved.** Record resolution inline (date + decision) and update the affected directive.

## 1. Volume-participation range — affects TASK-006

Reframe of "opening range" to stay compliant with the no-clock-logic rule: the range formed by the first X% of the day's cumulative volume, not the first N minutes.

- Options: include as specified / drop entirely / redefine
- **Status:** OPEN
- **Resolution:** —

## 2. DTE-graduated weighting — affects TASK-008

v1 spec = strict binary expiry/non-expiry config. Alternative: continuous DTE-based weighting (Monday behaves closer to Tuesday than Wednesday does).

- Options: keep binary for v1 (spec default) / graduated in v1
- **Status:** OPEN — spec default is binary; confirm
- **Resolution:** —

## 3. Historical backfill — affects TASK-001 scope, adds workstream if yes

Is backtesting against pre-launch dates a hard requirement? If yes → historical-data-sourcing workstream (NSE bhavcopy or paid vendor). If no → labeled dataset builds live from go-live forward.

- **Status:** RESOLVED (2026-07-03)
- **Resolution:** No. Live-forward only. Labeled dataset builds from go-live forward; no historical backfill workstream. TASK-001 scope unchanged.

## 4. Futures basis signal — affects TASK-002 (cheap add) + a signal module

Futures − spot and its drift through the session, as optional secondary positioning/sentiment signal. Direct futures feed already required, so marginal cost is low. Spec default: v2.

- **Status:** RESOLVED (2026-07-03)
- **Resolution:** Include now. TASK-002 exposes `futures_basis` (futures_ltp − spot_ltp) as a raw field on every ingestion snapshot. Session-drift interpretation (trend of basis over the day) is signal-module logic, not ingestion's job — deferred to whichever signal module consumes it (TASK-007 context, most likely), scoped when that module's ADR is written.
