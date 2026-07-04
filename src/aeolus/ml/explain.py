"""Top-feature attribution / reason-string templating (TASK-019 ADR).

Pure functions over TASK-018's already-computed z-vector (ScoreEvent.z_by_feature).
Ranking = descending |z|, tied by FEATURE_ORDER position (v1: plain per-feature
|z|, not Mahalanobis -- see ADR for the v2 upgrade path). Strictly downstream of
the flag decision: these functions take the outcome as input and never feed
back into it (constraint: no per-signal veto, no LLM-narrated text).
"""

from __future__ import annotations

from .features import FEATURE_ORDER

_FEATURE_INDEX: dict[str, int] = {name: idx for idx, name in enumerate(FEATURE_ORDER)}


def top_contributors(z_by_feature: dict[str, float], k: int = 3) -> list[tuple[str, float]]:
    """Descending |z|, ties broken by FEATURE_ORDER index. len <= k."""
    ranked = sorted(z_by_feature.items(), key=lambda pair: (-abs(pair[1]), _FEATURE_INDEX[pair[0]]))
    return ranked[:k]


def anomaly_reason(
    contributors: list[tuple[str, float]], score: float, flag_threshold: float, model_version: int
) -> str:
    """Deterministic template: same contributors/score/threshold/version -> same string."""
    if not contributors:
        raise ValueError("anomaly_reason requires at least one contributor")
    driven_by = ", ".join(f"{name} ({z:+.1f}σ)" for name, z in contributors)
    return f"anomalous — driven by {driven_by} | score {score:.3f} vs flag {flag_threshold:.3f} | model v{model_version}"


def clear_reason(score: float, clear_threshold: float, model_version: int) -> str:
    """Deterministic clear-event equivalent of anomaly_reason."""
    return f"cleared — score {score:.3f} vs clear {clear_threshold:.3f} | model v{model_version}"
