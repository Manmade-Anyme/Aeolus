"""ML feature extractor (TASK-015 ADR). Pure functions, no I/O, no state, no
fitting — a stored Isolation Forest is only valid against vectors built with
the exact feature set, order, and scaling it trained on.

Reads only the persisted SignalSnapshot (never IngestionSnapshot) so live
scoring (TASK-018) and EOD feature-store sync (TASK-016) share one code path.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from aeolus.storage.models import SignalSnapshot

SIGMA_FLOOR = 1e-9  # guard lives at fit time (TASK-017); standardize trusts the stored scaler


def _leaf(raw_readings: dict[str, Any], category: str, sub_signal: str) -> float | None:
    entry = raw_readings.get(category, {}).get(sub_signal)
    if entry is None:
        return None
    value = entry.get("raw_value")
    return float(value) if value is not None else None


def _context_leaf(raw_readings: dict[str, Any], category: str, sub_signal: str, key: str) -> float | None:
    entry = raw_readings.get(category, {}).get(sub_signal)
    if entry is None:
        return None
    context = entry.get("context")
    if not context:
        return None
    value = context.get(key)
    return float(value) if value is not None else None


def _gex_magnitude(snapshot: SignalSnapshot) -> float | None:
    raw = _leaf(snapshot.raw_readings, "gamma", "gex_regime")
    return abs(raw) if raw is not None else None


# Ordered mapping is the single source of truth for FEATURE_ORDER — no
# separate tuple to drift out of sync with the extraction dict. config_type
# selects the model downstream (TASK-018) and is deliberately absent here.
_ACCESSORS: dict[str, Callable[[SignalSnapshot], float | None]] = {
    "sub_score_volatility": lambda s: s.sub_scores.get("volatility"),
    "sub_score_gamma": lambda s: s.sub_scores.get("gamma"),
    "sub_score_oi_structure": lambda s: s.sub_scores.get("oi_structure"),
    "sub_score_order_flow": lambda s: s.sub_scores.get("order_flow"),
    "sub_score_context": lambda s: s.sub_scores.get("context"),
    "composite_score": lambda s: s.composite_score,
    "iv_percentile_rank": lambda s: _leaf(s.raw_readings, "volatility", "iv_percentile_rank"),
    "vix_level": lambda s: _leaf(s.raw_readings, "volatility", "vix_level_and_roc"),
    "vix_roc": lambda s: _context_leaf(s.raw_readings, "volatility", "vix_level_and_roc", "roc"),
    "pcr_level": lambda s: _context_leaf(s.raw_readings, "oi_structure", "pcr_level_and_roc", "pcr_level"),
    "pcr_roc": lambda s: _leaf(s.raw_readings, "oi_structure", "pcr_level_and_roc"),
    "gex_magnitude": _gex_magnitude,
    "spot_distance_from_flip": lambda s: _leaf(s.raw_readings, "gamma", "spot_distance_from_flip"),
    "expected_move_consumed_ratio": lambda s: _leaf(s.raw_readings, "volatility", "expected_move_consumed_ratio"),
    "cvd_divergence": lambda s: _leaf(s.raw_readings, "order_flow", "cvd_direction_and_divergence"),
}

FEATURE_ORDER: tuple[str, ...] = tuple(_ACCESSORS.keys())
FEATURE_SET_VERSION = 1


class Scaler(BaseModel):
    mean: dict[str, float]
    std: dict[str, float]  # sigma==0 in training -> stored as SIGMA_FLOOR (trainer's job)


def extract_features(snapshot: SignalSnapshot) -> dict[str, float | None] | None:
    """None if snapshot.system_status != "OK" (a broken feed is not a market
    anomaly). Else dict keyed exactly by FEATURE_ORDER; individual entries may
    be None (missing raw reading) — the extractor never imputes."""
    if snapshot.system_status != "OK":
        return None
    return {name: accessor(snapshot) for name, accessor in _ACCESSORS.items()}


def standardize(raw: dict[str, float], scaler: Scaler) -> list[float]:
    """z_i = (x_i - mu_i) / sigma_i, ordered by FEATURE_ORDER.
    Raises KeyError on any missing feature — callers filter Nones first."""
    return [(raw[name] - scaler.mean[name]) / scaler.std[name] for name in FEATURE_ORDER]
