"""ML Discord output (TASK-020 ADR). Formats and posts the four ML message
types -- anomaly advisory, anomaly cleared, warm-up progress, warm-up go-live
-- visually unmistakable vs engine messages (distinct `🔬 ML` prefix, distinct
embed colors, mandatory advisory footer). Pure presentation + delivery layer:
never decides *whether* to post (TASK-018's debounce/hysteresis and TASK-021's
hook decide that), only *how*. Mirrors aeolus.output.discord's shape but stays
a separate class in aeolus.ml so engine output code stays ML-free.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from aeolus.storage.models import ConfigType, SignalSnapshot

from .scorer import ScoreEvent

logger = logging.getLogger("aeolus.ml.output")

_TITLE_PREFIX = "\U0001f52c ML"  # microscope emoji -- never confusable with engine's plain "AEOLUS --" titles
_ADVISORY_FOOTER = "advisory only — does not change engine state"

# Deliberately outside aeolus.output.discord's palette (GO 0x2ECC71, PREPARE
# 0xF1C40F, NO_GO 0xE74C3C, SYSTEM_STATUS 0x9B59B6) so an ML message can never
# be mistaken for an engine one at a glance.
_ANOMALY_COLOR = 0x3498DB
_CLEAR_COLOR = 0x2C3E50
_WARMUP_COLOR = 0x7F8C8D
_GOLIVE_COLOR = 0x1ABC9C

_MAX_DESCRIPTION_LEN = 2000
_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)


class MLDiscordDeliveryError(Exception):
    """Raised after retry attempts are exhausted. Never swallowed internally --
    the caller (TASK-021's hook) catches and logs; a Discord outage must never
    propagate into the engine loop."""


def _truncate(text: str) -> str:
    if len(text) <= _MAX_DESCRIPTION_LEN:
        return text
    marker = "… (truncated)"
    return text[: _MAX_DESCRIPTION_LEN - len(marker)] + marker


class MLDiscordDispatcher:
    def __init__(self, webhook_url: str | None, *, transport: httpx.BaseTransport | None = None) -> None:
        self._webhook_url = webhook_url
        self._client = httpx.Client(transport=transport, timeout=httpx.Timeout(5.0)) if webhook_url else None
        # Per-config day-count of the last posted warm-up line. `day` is the
        # caller-supplied progress counter (strictly increases once per
        # session, per TASK-021), not a date read from the wall clock -- using
        # it as the dedup key avoids this module ever reading the clock at
        # all. Reset on process restart is an accepted tradeoff (ADR): a
        # mid-session restart may repeat one warm-up line.
        self._warmup_last_posted: dict[ConfigType, int] = {}
        if webhook_url is None:
            logger.warning("ML Discord output disabled: no webhook URL configured")

    def post_anomaly(self, event: ScoreEvent, reason: str, snapshot: SignalSnapshot) -> None:
        if self._client is None:
            return
        embed = {
            "title": f"{_TITLE_PREFIX} — Anomaly Flagged ({snapshot.config_type})",
            "description": _truncate(reason),
            "color": _ANOMALY_COLOR,
            "footer": {"text": _ADVISORY_FOOTER},
        }
        self._post({"embeds": [embed]})

    def post_clear(self, event: ScoreEvent, reason: str, config_type: ConfigType) -> None:
        if self._client is None:
            return
        embed = {
            "title": f"{_TITLE_PREFIX} — Anomaly Cleared ({config_type})",
            "description": _truncate(reason),
            "color": _CLEAR_COLOR,
        }
        self._post({"embeds": [embed]})

    def post_warmup_progress(self, config_type: ConfigType, day: int, target: int) -> None:
        """Max once per `day` per config (in-memory guard, see __init__)."""
        if self._client is None:
            return
        if self._warmup_last_posted.get(config_type) == day:
            return
        embed = {
            "title": f"{_TITLE_PREFIX} — Warm-up Progress ({config_type})",
            "description": f"still learning — day {day} of ~{target}",
            "color": _WARMUP_COLOR,
        }
        self._post({"embeds": [embed]})
        self._warmup_last_posted[config_type] = day

    def post_golive(self, config_type: ConfigType, model_version: int) -> None:
        if self._client is None:
            return
        embed = {
            "title": f"{_TITLE_PREFIX} — Model Live ({config_type})",
            "description": f"warm-up complete — model v{model_version} is now live, anomaly detection active",
            "color": _GOLIVE_COLOR,
        }
        self._post({"embeds": [embed]})

    def _post(self, payload: dict[str, Any]) -> None:
        assert self._client is not None and self._webhook_url is not None
        last_error: Exception | None = None
        for _attempt, backoff in enumerate((0.0,) + _RETRY_BACKOFF_SECONDS):
            if backoff:
                time.sleep(backoff)
            try:
                response = self._client.post(self._webhook_url, json=payload)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_error = exc
                continue

            if response.status_code < 300:
                return
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                if retry_after is not None:
                    time.sleep(min(float(retry_after), 10.0))
                last_error = MLDiscordDeliveryError(f"rate limited: {response.status_code}")
                continue
            if response.status_code >= 500:
                last_error = MLDiscordDeliveryError(f"server error: {response.status_code}")
                continue

            raise MLDiscordDeliveryError(f"non-retryable response {response.status_code}: {response.text}")

        raise MLDiscordDeliveryError(f"exhausted retries posting to Discord: {last_error}")
