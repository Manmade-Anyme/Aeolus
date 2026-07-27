"""OutlookGenerator (TASK-009 ADR) -- single pre-open invocation producing
Spec §5.1's probabilistic day-archetype forecast. Does its own Supabase I/O
(seeding + the daily_outlook write) but deliberately does NOT depend on
TASK-008's EngineState -- a smaller, dedicated query, see ADR Decision §2.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, cast

from supabase import Client, create_client

from aeolus.ingestion.models import IngestionSnapshot
from aeolus.signals import context as context_signals
from aeolus.signals import oi_structure as oi_structure_signals
from aeolus.signals import volatility as volatility_signals
from aeolus.signals.contract import _percentile_rank
from aeolus.storage.models import DailyOutlook, SignalSnapshot

from .archetype import primary_and_secondary, score_archetypes

from config.profiles import EXPIRY_CONFIG, NON_EXPIRY_CONFIG

logger = logging.getLogger("aeolus.outlook.generator")

# Matches EngineState.MAX_TRAILING_SESSIONS. Previously 20, which sat exactly
# on iv_percentile_rank's MIN_LOOKBACK_SESSIONS floor: a single seeded session
# missing `current_iv` in its carry left 19 samples and pinned the outlook's
# IV percentile to its "no data" 0.5 branch. It also meant the outlook and the
# engine ranked the same readings against different-length windows.
MAX_TRAILING_SESSIONS = 60


class OutlookGenerator:
    def __init__(self, supabase_url: str, supabase_key: str) -> None:
        self._client: Client = create_client(supabase_url, supabase_key)

    def run(self, session_date: date, snapshot: IngestionSnapshot) -> DailyOutlook:
        dte = context_signals.dte(session_date, snapshot.expiry_date)
        bands = (EXPIRY_CONFIG if dte == 0 else NON_EXPIRY_CONFIG).reference_bands

        prior = self._load_prior_day(session_date)
        trailing = self._load_trailing_histories(session_date)

        current_iv = volatility_signals._resolve_atm_iv(snapshot.option_chain, snapshot.spot_ltp)
        today_implied_move = volatility_signals.implied_expected_move(
            snapshot.spot_ltp, snapshot.india_vix
        )
        straddle_level_vs_history = _percentile_rank(
            today_implied_move, trailing["implied_move"]
        ) if today_implied_move is not None else 0.5

        iv_pct_result = volatility_signals.iv_percentile_rank(
            current_iv, trailing["iv"], bands["iv_percentile_rank"]
        )
        vix_result = volatility_signals.vix_level_and_roc(
            snapshot.india_vix, trailing["vix"], bands["vix_level_and_roc"]
        )
        vol_reads = [
            r[2] for r in (iv_pct_result, vix_result) if r[0] is not None
        ] + ([straddle_level_vs_history] if today_implied_move is not None else [])
        expanding_vol_pct = sum(vol_reads) / len(vol_reads) if vol_reads else None

        distribution = score_archetypes(
            profile_shape=prior.get("profile_shape"),
            expanding_vol_pct=expanding_vol_pct,
            dte=dte,
            gift_nifty_gap=(
                snapshot.gift_nifty - prior["close"]
                if snapshot.gift_nifty is not None and prior.get("close") is not None
                else None
            ),
            gift_gap_threshold=50.0,
        )
        primary, primary_prob, secondary, secondary_prob = primary_and_secondary(distribution)

        max_pain_result = oi_structure_signals.max_pain_drift(
            snapshot,
            prior.get("max_pain"),
            trailing["max_pain_drift"],
            bands["max_pain_drift"],
        )

        futures_gap = (
            snapshot.futures_ltp - prior["close"]
            if snapshot.futures_ltp is not None and prior.get("close") is not None
            else None
        )
        inside_prior_value_area = (
            prior.get("va_low") is not None
            and prior.get("va_high") is not None
            and snapshot.futures_ltp is not None
            and prior["va_low"] <= snapshot.futures_ltp <= prior["va_high"]
        )

        contributing_inputs: dict[str, Any] = {
            "archetype_distribution": distribution,
            "secondary_archetype": secondary,
            "secondary_confidence": secondary_prob,
            "gift_nifty_gap": snapshot.gift_nifty,
            "iv_percentile_heading_in": iv_pct_result[0],
            "vix_level_and_roc_heading_in": snapshot.india_vix,
            "oi_max_pain_carryover": max_pain_result[0],
            "prior_close_pcr_level": prior.get("pcr_level"),
            "futures_gap": futures_gap,
            "inside_prior_value_area": inside_prior_value_area,
            "dte": dte,
        }

        outlook = DailyOutlook(
            session_date=session_date,
            predicted_archetype=primary,  # type: ignore[arg-type]
            archetype_confidence=primary_prob,
            contributing_inputs=contributing_inputs,
            trend_exhaustion_flag=prior.get("profile_shape") == "trend",
            straddle_level_vs_history=straddle_level_vs_history,
        )
        payload = outlook.model_dump(mode="json")
        payload.pop("realized_archetype", None)  # never clobber a TASK-012 backfill

        existing = cast(
            "list[dict[str, Any]]",
            self._client.table(DailyOutlook.TABLE)
            .select("id")
            .eq("session_date", session_date.isoformat())
            .execute()
            .data,
        )
        if existing:
            payload["id"] = existing[0]["id"]  # update in place, don't rotate the primary key

        self._client.table(DailyOutlook.TABLE).upsert(
            payload, on_conflict="session_date"
        ).execute()
        return outlook

    def _load_prior_day(self, session_date: date) -> dict[str, Any]:
        rows = cast(
            "list[dict[str, Any]]",
            self._client.table(SignalSnapshot.TABLE)
            .select("session_date, ts, raw_readings")
            .lt("session_date", session_date.isoformat())
            .order("ts", desc=True)
            .limit(1)
            .execute()
            .data,
        )
        if not rows:
            return {}

        raw_readings = rows[0].get("raw_readings", {})
        context = raw_readings.get("context", {})
        context_carry = context.get("_carry", {})
        oi_structure = raw_readings.get("oi_structure", {})
        oi_carry = oi_structure.get("_carry", {})
        pcr_context = oi_structure.get("pcr_level_and_roc", {}).get("context", {})

        return {
            "profile_shape": context.get("profile_shape"),
            "day_high": context_carry.get("day_high"),
            "day_low": context_carry.get("day_low"),
            "close": context_carry.get("close"),
            "poc": context_carry.get("poc"),
            "va_low": context_carry.get("va_low"),
            "va_high": context_carry.get("va_high"),
            "max_pain": oi_carry.get("max_pain"),
            "pcr_level": pcr_context.get("pcr_level"),
        }

    def _load_trailing_histories(self, session_date: date) -> dict[str, list[float]]:
        try:
            rows = cast(
                "list[dict[str, Any]]",
                self._client.table(SignalSnapshot.EOD_VIEW)
                .select("session_date, ts, raw_readings")
                .lt("session_date", session_date.isoformat())
                .order("session_date", desc=True)
                .limit(MAX_TRAILING_SESSIONS)
                .execute()
                .data,
            )
        except Exception:
            # Same degraded-but-safe fallback as EngineState.load() -- see the
            # comment there. Logged at ERROR: it means migration 0013 is missing.
            logger.exception(
                "Seeding trailing histories from %s failed; falling back to %s. "
                "The outlook will run on single-session history until "
                "migration 0013 is applied.",
                SignalSnapshot.EOD_VIEW,
                SignalSnapshot.TABLE,
            )
            rows = cast(
                "list[dict[str, Any]]",
                self._client.table(SignalSnapshot.TABLE)
                .select("session_date, ts, raw_readings")
                .lt("session_date", session_date.isoformat())
                .order("session_date", desc=True)
                .order("ts", desc=True)
                .limit(MAX_TRAILING_SESSIONS * 20)
                .execute()
                .data,
            )

        last_row_per_date: dict[str, dict[str, Any]] = {}
        for row in rows:
            last_row_per_date.setdefault(row["session_date"], row)

        ordered_dates = sorted(last_row_per_date, reverse=True)[:MAX_TRAILING_SESSIONS]
        daily_rows = [last_row_per_date[d] for d in reversed(ordered_dates)]

        iv_history: list[float] = []
        vix_history: list[float] = []
        implied_move_history: list[float] = []
        max_pain_drift_history: list[float] = []

        for row in daily_rows:
            raw_readings = row.get("raw_readings", {})
            vol_carry = raw_readings.get("volatility", {}).get("_carry", {})
            current_iv = vol_carry.get("current_iv")
            spot_ltp = vol_carry.get("spot_ltp")
            india_vix = vol_carry.get("india_vix")

            if current_iv is not None:
                iv_history.append(current_iv)
            if india_vix is not None:
                vix_history.append(india_vix)
            implied_move = volatility_signals.implied_expected_move(spot_ltp, india_vix)
            if implied_move is not None:
                implied_move_history.append(implied_move)

            max_pain_reading = raw_readings.get("oi_structure", {}).get("max_pain_drift", {})
            if max_pain_reading.get("raw_value") is not None:
                max_pain_drift_history.append(abs(max_pain_reading["raw_value"]))

        return {
            "iv": iv_history,
            "vix": vix_history,
            "implied_move": implied_move_history,
            "max_pain_drift": max_pain_drift_history,
        }
