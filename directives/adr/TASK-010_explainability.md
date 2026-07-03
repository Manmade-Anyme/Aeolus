# Architecture Decision Record — TASK-010

**Directive:** `directives/TASK-010_explainability.md`
**Status:** APPROVED (2026-07-03)
**Date:** 2026-07-03

## Problem

Promote `src/aeolus/explain/reason.py` from TASK-003's interim stub to the real, canonical reason-string templater every category module (TASK-003..007) already calls, and add the one piece that doesn't exist yet: a composite-level transition explainer that cites the category/categories whose sub-score movement actually drove a state flip (Spec §10, constraint #3).

Two things are already true and must not change:
1. `template_reason(signal_name, raw_value, reference_band, sub_score, context=None) -> str` — signature and output format are load-bearing. TASK-003..007 call this today; the directive itself requires zero changes to signal modules when TASK-010 lands, so this ADR keeps the signature and formatting byte-identical.
2. `engine.py::_extract_context` round-trips data (e.g. `established_range`) through the `[key=val]` context suffix. That mechanism is TASK-008 ADR §4's territory, not this one's — untouched here.

## Decision

**`template_reason` — no behavior change, status change only.** Drop "interim stub" framing from the docstring; this is now the real module. Formatting stays pinned at 2-decimal rounding for determinism (already correct). No new edge cases surfaced beyond what TASK-003 already handled (`None` raw_value → explicit `"{signal_name}: no data"`).

**New: `explain_transition(trigger_categories: list[str], composite_score: float, confirmation_cycles: int) -> str`** in the same module. This is the composite-level counterpart to `template_reason`, filling the directive's fourth acceptance criterion. Deterministic string, built only from its three inputs — no free text.

- `trigger_categories` — **not computed here.** TASK-008's `engine.py` already selects these (`abs(category_score - 0.5) >= 0.1`, engine.py:369-371) before calling this function. TASK-010's job is templating the string, not deciding which categories qualify — same division of labor as `template_reason`, which takes `sub_score` as given rather than computing it. Keeps the "which categories drove this" judgment call inside TASK-008's ADR, where the threshold constant lives.
- Empty `trigger_categories` (composite crossed a threshold on aggregate drift with no single category individually deviating past the 0.1 margin) renders as `"broad-based shift across categories"` rather than an empty citation — still deterministic, still traces to the data (absence of an outlier), never fabricated.
- Format: `"{from}->{to} driven by {sorted, comma-joined categories}; composite={score:.2f}, confirmed after {N} cycles"`. Categories sorted alphabetically for determinism (set/dict iteration order is not guaranteed to be caller-stable). `from`/`to` are NOT passed as separate params — see API Contracts; caller (`engine.py`) formats those into the same call.

**Wiring change in `engine.py` (mechanical, not a redesign):** replace the current inline
```python
reason=f"composite={composite:.2f} confirmed after {config.confirmation_cycles} cycles",
```
with a call to `explain.reason.explain_transition(...)`, passing `previous_confirmed_state`, `confirmed_state`, `trigger_categories`, `composite`, `config.confirmation_cycles`. This is the only `engine.py` change TASK-010 makes — `trigger_categories` computation (line 369-371) is untouched.

**Property test (directive AC: "property test this"):** add `hypothesis` as a dev dependency. Property: for arbitrary `(signal_name, raw_value, reference_band, sub_score, context)` inputs, two calls with identical inputs produce byte-identical strings, and `raw_value=None` always produces exactly `"{signal_name}: no data"` regardless of the other params. Same property (call twice, compare) for `explain_transition`.

**Alternative considered:** have `explain_transition` recompute `trigger_categories` itself from raw category scores, taking the threshold as a parameter. Rejected — duplicates a decision (`0.1` margin) that's already made and owned by TASK-008's ADR; TASK-010 should template, not re-decide which categories qualify.

**Alternative considered:** LLM-assisted phrasing for the composite explanation (more natural sentence). Rejected outright — constraint #3 is unconditional, no exception for the composite level.

## Component Boundaries

| File | Responsibility |
|------|---|
| `src/aeolus/explain/reason.py` | `template_reason` (unchanged behavior, destubbed) + new `explain_transition` |
| `src/aeolus/engine/engine.py` | One-line call-site swap at the `StateTransition.reason` construction; `trigger_categories` selection logic untouched |
| `tests/explain/test_reason.py` | Existing 3 tests kept as-is; add `explain_transition` tests + hypothesis property tests |
| `pyproject.toml` | Add `hypothesis` to `[project.optional-dependencies].dev` |

## API Contracts

```python
def template_reason(
    signal_name: str,
    raw_value: float | None,
    reference_band: tuple[float, float],
    sub_score: float,
    context: dict[str, float] | None = None,
) -> str:
    """Unchanged from TASK-003 stub. raw_value is None -> '{signal_name}: no data'."""

def explain_transition(
    from_state: str,
    to_state: str,
    trigger_categories: list[str],
    composite_score: float,
    confirmation_cycles: int,
) -> str:
    """Deterministic. trigger_categories is caller-selected (TASK-008's threshold),
    not recomputed here. Empty list -> 'broad-based shift across categories'.
    """
```
Signal modules MUST use the standard contract: `(raw_value, reference_band, sub_score, reason_string)` — already satisfied, no change.

## Performance / Failure Modes

Pure string formatting, no I/O, no exceptions expected from valid typed inputs (mirrors `template_reason`'s existing no-try/except design — a bad input here is a caller bug, not a runtime condition to degrade gracefully from, consistent with how `template_reason` already behaves for its own params).

## Definition of Done

- [ ] Integration-style tests against `template_reason` (existing, unchanged) and new `explain_transition` — no internal mocking
- [ ] Hypothesis property test: determinism holds for both functions across generated inputs
- [ ] `engine.py` `StateTransition.reason` cites trigger categories (previously generic `"composite=X confirmed after N cycles"` with no category names — this was the actual gap TASK-010 closes)
- [ ] Constraint check: no per-signal veto (n/a, pure templating), no clock logic (n/a), deterministic reasons (yes — this module IS constraint #3), polarity correct (n/a, no scoring here)
