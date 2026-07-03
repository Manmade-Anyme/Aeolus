"""Discord output formatter + dispatch (TASK-011 ADR). Formats the three
Spec §12 message types and posts them via webhook. Pure presentation layer --
no scoring, no state logic (TASK-008 already decides what counts as a
genuine transition; this module only renders and sends what it's given).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from aeolus.storage.models import DailyOutlook, DayArchetype, MarketState, SignalSnapshot, StateTransition, SystemStatus

from config.tuning import SUB_SIGNAL_NAMES

_MARKET_STATE_COLOR: dict[MarketState, int] = {
    "GO": 0x2ECC71,
    "PREPARE": 0xF1C40F,
    "NO_GO": 0xE74C3C,
}
_SYSTEM_STATUS_COLOR = 0x9B59B6  # deliberately outside the GO/PREPARE/NO_GO palette

ARCHETYPE_STATE_LEAN: dict[DayArchetype, str] = {
    "clean_trend": "GO",
    "grinding_trend": "NO_GO",
    "pinned_range": "NO_GO",
    "choppy_range": "NO_GO",
    "breakout_transition": "mixed",
    "event_gap": "mixed",
    "double_distribution": "NO_GO",
}

_MAX_FIELD_LEN = 1024
_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)


class DiscordDeliveryError(Exception):
    """Raised after retry attempts are exhausted. Never swallowed internally --
    the caller (TASK-013 scheduler) decides what happens to a failed post."""


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _truncate(text: str) -> str:
    if len(text) <= _MAX_FIELD_LEN:
        return text
    marker = "… (truncated)"
    return text[: _MAX_FIELD_LEN - len(marker)] + marker


def _confirm_diverge_note(to_state: MarketState, outlook: DailyOutlook | None) -> str:
    if outlook is None:
        return "no Outlook available for today"
    lean = ARCHETYPE_STATE_LEAN[outlook.predicted_archetype]
    if lean == "mixed":
        return f"{outlook.predicted_archetype} outlook is not directly comparable to today's Outlook"
    if to_state == "PREPARE":
        return f"partially confirms {outlook.predicted_archetype} outlook (lean {lean})"
    if to_state == lean:
        return f"confirms {outlook.predicted_archetype} outlook"
    return f"diverges from {outlook.predicted_archetype} outlook (lean {lean})"


def _category_breakdown_fields(snapshot: SignalSnapshot) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for category, readings in snapshot.raw_readings.items():
        sub_signal_names = [name for name in readings if name in SUB_SIGNAL_NAMES]
        lines = [snapshot.reasons.get(name, f"{name}: unavailable") for name in sub_signal_names]
        score = snapshot.sub_scores.get(category)
        header = f"score={_fmt(score)}"
        value = _truncate(f"{header}\n" + "\n".join(lines) if lines else header)
        fields.append({"name": category, "value": value, "inline": False})
    return fields


class DiscordDispatcher:
    def __init__(
        self,
        market_webhook_url: str,
        status_webhook_url: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._market_webhook_url = market_webhook_url
        self._status_webhook_url = status_webhook_url
        self._client = httpx.Client(transport=transport, timeout=httpx.Timeout(5.0))

    def post_outlook(self, outlook: DailyOutlook) -> None:
        inputs = outlook.contributing_inputs
        fields = [
            {
                "name": "Forecast",
                "value": (
                    f"Primary: {outlook.predicted_archetype} ({_fmt(outlook.archetype_confidence)})\n"
                    f"Secondary: {_fmt(inputs.get('secondary_archetype'))} "
                    f"({_fmt(inputs.get('secondary_confidence'))})"
                ),
                "inline": False,
            },
            {
                "name": "Contributing inputs",
                "value": _truncate(
                    "\n".join(
                        f"{key}={_fmt(inputs.get(key))}"
                        for key in (
                            "gift_nifty_gap",
                            "iv_percentile_heading_in",
                            "vix_level_and_roc_heading_in",
                            "oi_max_pain_carryover",
                            "prior_close_pcr_level",
                            "futures_gap",
                            "inside_prior_value_area",
                            "dte",
                        )
                    )
                    + f"\nstraddle_level_vs_history={_fmt(outlook.straddle_level_vs_history)}"
                ),
                "inline": False,
            },
        ]
        description = None
        if outlook.trend_exhaustion_flag:
            description = (
                "⚠ Yesterday resolved as a clean/elongated trend day -- "
                "elevated prior for digestion/consolidation today."
            )
        embed: dict[str, Any] = {
            "title": f"AEOLUS -- Pre-Market Outlook ({outlook.session_date.isoformat()})",
            "color": _MARKET_STATE_COLOR["PREPARE"],
            "fields": fields,
        }
        if description:
            embed["description"] = description
        self._post(self._market_webhook_url, {"embeds": [embed]})

    def post_transition(
        self,
        transition: StateTransition,
        snapshot: SignalSnapshot,
        outlook: DailyOutlook | None,
    ) -> None:
        fields = _category_breakdown_fields(snapshot)
        fields.append(
            {
                "name": "vs Morning Outlook",
                "value": _confirm_diverge_note(transition.to_state, outlook),
                "inline": False,
            }
        )
        embed = {
            "title": f"AEOLUS -- {transition.from_state} -> {transition.to_state}",
            "description": f"composite={_fmt(snapshot.composite_score)} | {transition.reason}",
            "color": _MARKET_STATE_COLOR[transition.to_state],
            "fields": fields,
        }
        self._post(self._market_webhook_url, {"embeds": [embed]})

    def post_system_status(self, status: SystemStatus, previous_status: SystemStatus) -> None:
        embed = {
            "title": "⚠ AEOLUS SYSTEM STATUS",
            "description": f"{previous_status} -> {status}",
            "color": _SYSTEM_STATUS_COLOR,
        }
        self._post(self._status_webhook_url, {"embeds": [embed]})

    def _post(self, url: str, payload: dict[str, Any]) -> None:
        last_error: Exception | None = None
        for attempt, backoff in enumerate((0.0,) + _RETRY_BACKOFF_SECONDS):
            if backoff:
                time.sleep(backoff)
            try:
                response = self._client.post(url, json=payload)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_error = exc
                continue

            if response.status_code < 300:
                return
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                if retry_after is not None:
                    time.sleep(min(float(retry_after), 10.0))
                last_error = DiscordDeliveryError(f"rate limited: {response.status_code}")
                continue
            if response.status_code >= 500:
                last_error = DiscordDeliveryError(f"server error: {response.status_code}")
                continue

            raise DiscordDeliveryError(
                f"non-retryable response {response.status_code}: {response.text}"
            )

        raise DiscordDeliveryError(f"exhausted retries posting to Discord: {last_error}")
