"""MLTuning (TASK-017 ADR) -- ML overlay tuning knobs, pydantic-settings style
mirroring config/tuning.py, housed in aeolus.ml (not config/) to preserve the
engine/ML boundary. Extended by later tasks (TASK-018's dwell/hysteresis
knobs, TASK-020's webhook config) rather than pre-declared here.

Defaults resolved via OPEN_DECISIONS #7-9 (2026-07-04): 30-day rolling
window, flag top 5% / clear top 10% hysteresis pair, daily EOD retrain
cadence (cadence is a scheduling concern enforced by TASK-021, not a value
read here).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class MLTuning(BaseSettings):
    window_days: int = 30
    flag_pct: float = 0.05
    clear_pct: float = 0.10
    warmup_min_samples_factor: int = 10
    warmup_min_days: int = 15
    n_estimators: int = 200
    random_state: int = 42
    model_cache_ttl_seconds: int = 300
    min_dwell_cycles: int = 0  # 0 = off; N blocks any state flip until current state held >= N cycles
