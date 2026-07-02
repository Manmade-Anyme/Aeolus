# Architecture Decision Record — TASK-###

**Directive:** `directives/TASK-###_*.md`
**Status:** DRAFT | APPROVED | SUPERSEDED
**Date:** YYYY-MM-DD

## Problem
[Restate the directive goal in technical terms]

## Decision
[High-level design, 2–3 paragraphs. Name the alternatives considered and why rejected.]

## Component Boundaries
| File | Responsibility |
|------|---|
| src/aeolus/.../module.py | Core logic |

## API Contracts
```python
def do_thing(input: X) -> Y:
    """Input: ... Output: ... Raises: ..."""
```
Signal modules MUST use the standard contract: `(raw_value, reference_band, sub_score, reason_string)`.

## Performance / Failure Modes
[Latency budget, staleness handling, what happens on partial data]

## Definition of Done
- [ ] Integration-style tests against the public contracts above (no internal mocking)
- [ ] [Contract-specific test case 1]
- [ ] Constraint check: no per-signal veto, no clock logic, deterministic reasons, polarity correct
