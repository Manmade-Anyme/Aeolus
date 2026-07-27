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

_CATEGORY_LABELS: dict[str, str] = {
    "volatility": "Volatility",
    "gamma": "Gamma",
    "oi_structure": "OI Structure",
    "order_flow": "Order Flow",
    "context": "Context",
}

_INPUT_LABELS: dict[str, str] = {
    "gift_nifty_gap": "GIFT Nifty Gap",
    "iv_percentile_heading_in": "IV Percentile (heading in)",
    "vix_level_and_roc_heading_in": "VIX Level & RoC (heading in)",
    "oi_max_pain_carryover": "OI Max Pain (carryover)",
    "prior_close_pcr_level": "Prior Close PCR",
    "futures_gap": "Futures Gap",
    "inside_prior_value_area": "Inside Prior Value Area",
    "dte": "DTE",
}

_SUB_SIGNAL_LABELS: dict[str, str] = {
    "iv_percentile_rank": "IV Percentile Rank",
    "iv_rv_spread": "IV-RV Spread",
    "vix_level_and_roc": "VIX Level & RoC",
    "expected_move_consumed_ratio": "Expected Move Consumed",
    "gex_regime": "GEX Regime",
    "spot_distance_from_flip": "Distance from Gamma Flip",
    "pcr_level_and_roc": "PCR Level & RoC",
    "oi_buildup_classification": "OI Buildup",
    "oi_wall_proximity_and_strength": "OI Wall Proximity",
    "max_pain_drift": "Max Pain Drift",
    "cvd_direction_and_divergence": "CVD Direction/Divergence",
    "delta_imbalance_and_absorption": "Delta Imbalance/Absorption",
    "volume_participation_range": "Volume Participation",
    "prior_day_profile_shape": "Prior Day Profile",
    "gap_classification": "Gap Classification",
    "futures_basis_drift": "Futures Basis Drift",
}

_ARCHETYPE_LABELS: dict[str, str] = {
    "clean_trend": "Clean Trend",
    "grinding_trend": "Grinding Trend",
    "pinned_range": "Pinned Range",
    "choppy_range": "Choppy Range",
    "breakout_transition": "Breakout Transition",
    "event_gap": "Event Gap",
    "double_distribution": "Double Distribution",
}


def _archetype_label(value: Any) -> str:
    if not isinstance(value, str):
        return _fmt(value)
    return _ARCHETYPE_LABELS.get(value, value)


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


# ANSI color codes inside ```ansi fenced blocks only render on Discord
# desktop/web, not mobile (confirmed 2026-07-04) -- emoji indicators instead,
# identical rendering everywhere.
_FAVORABLE_EMOJI = "\U0001f7e2"  # green
_NEUTRAL_EMOJI = "\U0001f7e1"  # yellow
_UNFAVORABLE_EMOJI = "\U0001f534"  # red

# Same favorable/neutral/unfavorable band engine.py already uses to pick
# trigger_categories (abs(score - 0.5) >= 0.1) -- reused here, not reinvented.
_FAVORABLE_THRESHOLD = 0.6
_UNFAVORABLE_THRESHOLD = 0.4


def _score_emoji(score: float | None) -> str:
    if score is None:
        return ""
    if score >= _FAVORABLE_THRESHOLD:
        return _FAVORABLE_EMOJI
    if score <= _UNFAVORABLE_THRESHOLD:
        return _UNFAVORABLE_EMOJI
    return _NEUTRAL_EMOJI


_GEX_REGIME_TYPE: dict[bool, str] = {
    True: "Short Gamma / Trending",
    False: "Long Gamma / Pinning",
}


def _gex_regime_type(raw_value: float | None) -> str | None:
    if raw_value is None or raw_value == 0:
        return None
    return _GEX_REGIME_TYPE[raw_value < 0]


def _confirm_diverge_note(to_state: MarketState, outlook: DailyOutlook | None) -> str:
    if outlook is None:
        return "no Outlook available for today"
    archetype = _archetype_label(outlook.predicted_archetype)
    lean = ARCHETYPE_STATE_LEAN[outlook.predicted_archetype]
    if lean == "mixed":
        return f"{archetype} outlook is not directly comparable to today's Outlook"
    if to_state == "PREPARE":
        return f"partially confirms {archetype} outlook (lean {lean})"
    if to_state == lean:
        return f"confirms {archetype} outlook"
    return f"diverges from {archetype} outlook (lean {lean})"


def _score_line(name: str, entry: Any) -> str:
    """Score-only display -- no raw_value/reference_band clutter (per human
    feedback: readable score number, not the underlying config). gex_regime
    gets an extra regime-type annotation (short-gamma/trending vs
    long-gamma/pinning), derived from its raw_value's sign."""
    label = _SUB_SIGNAL_LABELS.get(name, name)
    if not isinstance(entry, dict) or entry.get("raw_value") is None:
        return f"{label}: no data"
    sub_score = entry.get("sub_score")
    text = f"{label}: {_fmt(sub_score)}"
    if name == "gex_regime":
        regime = _gex_regime_type(entry.get("raw_value"))
        if regime:
            text = f"{text} ({regime})"
    return text


def _category_breakdown_fields(snapshot: SignalSnapshot) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for category, readings in snapshot.raw_readings.items():
        sub_signal_names = [name for name in readings if name in SUB_SIGNAL_NAMES]
        bullets = []
        for name in sub_signal_names:
            entry = readings.get(name)
            sub_score = entry.get("sub_score") if isinstance(entry, dict) else None
            emoji = _score_emoji(sub_score)
            prefix = f"{emoji} " if emoji else "• "
            bullets.append(f"{prefix}{_score_line(name, entry)}")
        score = snapshot.sub_scores.get(category)
        header_emoji = _score_emoji(score)
        header = f"{header_emoji} score = {_fmt(score)}" if header_emoji else f"score = {_fmt(score)}"
        value = _truncate("\n".join([header] + bullets) if bullets else header)
        name = _CATEGORY_LABELS.get(category, category)
        fields.append({"name": name, "value": value, "inline": False})
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
                    f"• **Primary:** {_archetype_label(outlook.predicted_archetype)} "
                    f"({_fmt(outlook.archetype_confidence)})\n"
                    f"• **Secondary:** {_archetype_label(inputs.get('secondary_archetype'))} "
                    f"({_fmt(inputs.get('secondary_confidence'))})"
                ),
                "inline": False,
            },
            {
                "name": "Contributing inputs",
                "value": _truncate(
                    "\n".join(
                        f"• **{label}:** {_fmt(inputs.get(key))}" for key, label in _INPUT_LABELS.items()
                    )
                    + f"\n• **Straddle Level vs History:** {_fmt(outlook.straddle_level_vs_history)}"
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
        from aeolus.explain.regime import classify_regime_and_suitability
        from config.profiles import EXPIRY_CONFIG, NON_EXPIRY_CONFIG

        config = EXPIRY_CONFIG if snapshot.config_type == "EXPIRY" else NON_EXPIRY_CONFIG
        regime_name, suitability_status, suitability_emoji, explanation = classify_regime_and_suitability(
            snapshot.raw_readings, snapshot.sub_scores, snapshot.composite_score, thresholds=config.thresholds
        )

        executive_field = {
            "name": f"📊 MARKET REGIME: {regime_name}",
            "value": (
                f"**Option Buyer Suitability:** {suitability_emoji} **{suitability_status}**\n\n"
                f"**Executive Summary:** {explanation}"
            ),
            "inline": False,
        }

        fields = [executive_field] + _category_breakdown_fields(snapshot)
        fields.append(
            {
                "name": "vs Morning Outlook",
                "value": f"• {_confirm_diverge_note(transition.to_state, outlook)}",
                "inline": False,
            }
        )
        embed = {
            "title": f"AEOLUS -- {transition.from_state} -> {transition.to_state}",
            "description": transition.reason,
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
