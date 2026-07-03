"""Engine (TASK-008 ADR) -- the composite scorer + state machine. Public
entrypoint TASK-013 (scheduler) calls: start() once, run_cycle() every tick,
end_session() once at market close. This is the first module in the project
that does real I/O (Supabase) -- every prior signal module (TASK-003..007)
is a pure function suite.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from supabase import Client, create_client

from aeolus.explain.reason import explain_transition
from aeolus.ingestion.models import IngestionSnapshot
from aeolus.signals import context as context_signals
from aeolus.signals import gamma as gamma_signals
from aeolus.signals import oi_structure as oi_structure_signals
from aeolus.signals import order_flow as order_flow_signals
from aeolus.signals import volatility as volatility_signals
from aeolus.storage.models import SignalSnapshot, StateTransition

from .scorer import apply_hysteresis, category_score, composite_score, safe_call, state_for_score
from .state import EngineState

from config.profiles import EXPIRY_CONFIG, NON_EXPIRY_CONFIG
from config.tuning import EngineConfig

_CONTEXT_SUFFIX_RE = re.compile(r"\[(.+)\]$")


def _extract_context(reason: str) -> dict[str, float]:
    """Recovers template_reason's context dict from its deterministic
    '[key=val, key2=val2]' suffix -- the only channel volume_participation_range
    has for handing established_range back to its caller (ADR §4)."""
    match = _CONTEXT_SUFFIX_RE.search(reason)
    if not match:
        return {}
    result: dict[str, float] = {}
    for pair in match.group(1).split(", "):
        key, _, value = pair.partition("=")
        try:
            result[key] = float(value)
        except ValueError:
            continue
    return result


class Engine:
    def __init__(self, supabase_url: str, supabase_key: str) -> None:
        self._client: Client = create_client(supabase_url, supabase_key)
        self._state = EngineState()
        self._session_date: date | None = None
        self._previous_iv: float | None = None

    def start(self, session_date: date) -> None:
        """Seeds EngineState from Supabase. Raises on a Supabase failure --
        never partially starts. Config selection happens per-cycle in
        run_cycle (DTE isn't known until the first IngestionSnapshot)."""
        self._session_date = session_date
        self._state = EngineState.load(self._client, session_date)
        self._previous_iv = None

    def end_session(self) -> None:
        """Called once by TASK-013 at market close (3:31pm IST). See ADR §7."""
        self._state.clear_session_scoped()
        self._previous_iv = None

    def run_cycle(self, snapshot: IngestionSnapshot, lot_size: int) -> SignalSnapshot:
        if self._session_date is None:
            raise RuntimeError("Engine.start() must be called first")

        state = self._state
        dte = context_signals.dte(self._session_date, snapshot.expiry_date)
        config_type: str = "EXPIRY" if dte == 0 else "NON_EXPIRY"
        config: EngineConfig = EXPIRY_CONFIG if dte == 0 else NON_EXPIRY_CONFIG
        bands = config.reference_bands

        previous = state.previous_snapshot
        raw_volume_delta = (
            snapshot.volume - previous.volume
            if previous is not None and snapshot.volume is not None and previous.volume is not None
            else None
        )

        if state.session_open is None and snapshot.futures_ltp is not None:
            state.session_open = snapshot.futures_ltp
        if state.session_reference_price is None and snapshot.spot_ltp is not None:
            state.session_reference_price = snapshot.spot_ltp

        raw_readings: dict[str, dict[str, Any]] = {}
        reasons: dict[str, str] = {}

        # --- volatility ---
        current_iv = volatility_signals._resolve_atm_iv(snapshot.option_chain, snapshot.spot_ltp)
        straddle_implied_expected_move = volatility_signals.implied_expected_move(
            snapshot.spot_ltp, snapshot.india_vix
        )
        volatility_results = {
            "iv_percentile_rank": safe_call(
                "iv_percentile_rank",
                volatility_signals.iv_percentile_rank,
                current_iv,
                state.trailing_iv_history,
                bands["iv_percentile_rank"],
            ),
            "iv_rv_spread": safe_call(
                "iv_rv_spread",
                volatility_signals.iv_rv_spread,
                current_iv,
                self._previous_iv,
                state.trailing_spot_history,
                state.trailing_iv_rising_magnitude_history,
                state.trailing_iv_falling_magnitude_history,
                bands["iv_rv_spread"],
            ),
            "vix_level_and_roc": safe_call(
                "vix_level_and_roc",
                volatility_signals.vix_level_and_roc,
                snapshot.india_vix,
                state.trailing_vix_history,
                bands["vix_level_and_roc"],
            ),
            "expected_move_consumed_ratio": safe_call(
                "expected_move_consumed_ratio",
                volatility_signals.expected_move_consumed_ratio,
                snapshot.spot_ltp,
                state.session_reference_price,
                straddle_implied_expected_move,
                bands["expected_move_consumed_ratio"],
            ),
        }
        raw_readings["volatility"] = _category_raw_readings(volatility_results)
        raw_readings["volatility"]["_carry"] = _drop_none(
            {"current_iv": current_iv, "spot_ltp": snapshot.spot_ltp, "india_vix": snapshot.india_vix}
        )

        # --- gamma ---
        gamma_results = {
            "gex_regime": safe_call(
                "gex_regime",
                gamma_signals.gex_regime,
                snapshot.option_chain,
                snapshot.spot_ltp,
                lot_size,
                state.trailing_gex_magnitude_history,
                bands["gex_regime"],
                min_total_oi=config.min_total_oi,
            ),
            "spot_distance_from_flip": safe_call(
                "spot_distance_from_flip",
                gamma_signals.spot_distance_from_flip,
                snapshot.option_chain,
                snapshot.spot_ltp,
                bands["spot_distance_from_flip"],
                min_total_oi=config.min_total_oi,
            ),
        }
        raw_readings["gamma"] = _category_raw_readings(gamma_results)

        # --- oi_structure ---
        current_max_pain = oi_structure_signals._max_pain(snapshot.option_chain)
        if state.session_open_max_pain is None and current_max_pain is not None:
            state.session_open_max_pain = current_max_pain
        oi_results = {
            "pcr_level_and_roc": safe_call(
                "pcr_level_and_roc",
                oi_structure_signals.pcr_level_and_roc,
                snapshot,
                previous,
                state.trailing_pcr_roc_magnitude_history,
                bands["pcr_level_and_roc"],
            ),
            "oi_buildup_classification": safe_call(
                "oi_buildup_classification",
                oi_structure_signals.oi_buildup_classification,
                snapshot,
                previous,
                bands["oi_buildup_classification"],
            ),
            "oi_wall_proximity_and_strength": safe_call(
                "oi_wall_proximity_and_strength",
                oi_structure_signals.oi_wall_proximity_and_strength,
                snapshot,
                previous,
                state.trailing_wall_proximity_history,
                state.trailing_wall_strength_trend_history,
                bands["oi_wall_proximity_and_strength"],
            ),
            "max_pain_drift": safe_call(
                "max_pain_drift",
                oi_structure_signals.max_pain_drift,
                snapshot,
                state.session_open_max_pain,
                state.trailing_max_pain_drift_history,
                bands["max_pain_drift"],
            ),
        }
        raw_readings["oi_structure"] = _category_raw_readings(oi_results)
        raw_readings["oi_structure"]["_carry"] = _drop_none({"max_pain": current_max_pain})

        # --- order_flow ---
        cvd_delta = order_flow_signals._tick_classify(snapshot, previous)
        if cvd_delta is not None:
            state.cvd_delta_history.append(cvd_delta)
        if snapshot.futures_ltp is not None:
            state.price_history.append(snapshot.futures_ltp)
        if snapshot.futures_basis is not None:
            state.basis_history.append(snapshot.futures_basis)

        average_daily_volume = (
            state.average_daily_volume if state.average_daily_volume is not None else float("inf")
        )
        volume_result = safe_call(
            "volume_participation_range",
            order_flow_signals.volume_participation_range,
            snapshot,
            state.established_range,
            average_daily_volume,
            config.volume_participation_pct,
            config.min_range_width,
            state.trailing_excursion_history,
            bands["volume_participation_range"],
        )
        established = _extract_context(volume_result[3])
        if "established_low" in established:
            state.established_range = (established["established_low"], established["established_high"])

        order_flow_results = {
            "cvd_direction_and_divergence": safe_call(
                "cvd_direction_and_divergence",
                order_flow_signals.cvd_direction_and_divergence,
                state.cvd_delta_history,
                state.price_history,
                state.trailing_cvd_magnitude_history,
                bands["cvd_direction_and_divergence"],
            ),
            "delta_imbalance_and_absorption": safe_call(
                "delta_imbalance_and_absorption",
                order_flow_signals.delta_imbalance_and_absorption,
                snapshot,
                state.trailing_imbalance_magnitude_history,
                config.order_flow_extreme_threshold,
                bands["delta_imbalance_and_absorption"],
            ),
            "volume_participation_range": volume_result,
        }
        raw_readings["order_flow"] = _category_raw_readings(order_flow_results)
        raw_readings["order_flow"]["_carry"] = _drop_none(
            {
                "cvd_delta": cvd_delta,
                "futures_ltp": snapshot.futures_ltp,
                "futures_basis": snapshot.futures_basis,
                "volume": snapshot.volume,
                "established_range_low": state.established_range[0]
                if state.established_range
                else None,
                "established_range_high": state.established_range[1]
                if state.established_range
                else None,
            }
        )

        # --- context ---
        if raw_volume_delta is not None and snapshot.futures_ltp is not None:
            state.cycle_price_volume_history.append((snapshot.futures_ltp, raw_volume_delta))
        histogram = context_signals.build_volume_price_histogram(
            state.cycle_price_volume_history, config.bucket_size
        )
        current_value_area = context_signals.value_area(histogram, config.area_pct)

        profile_shape, profile_result = safe_call_profile_shape(
            state.prior_day_high,
            state.prior_day_low,
            state.prior_close,
            state.prior_value_area,
            state.trailing_average_range_history,
            config.expansion_threshold,
            config.context_extreme_threshold,
            bands["prior_day_profile_shape"],
        )
        context_results = {
            "gap_classification": safe_call(
                "gap_classification",
                context_signals.gap_classification,
                state.session_open,
                snapshot.futures_ltp,
                state.prior_value_area,
                state.prior_close,
                state.trailing_gap_magnitude_history,
                bands["gap_classification"],
            ),
            "prior_day_profile_shape": profile_result,
            "futures_basis_drift": safe_call(
                "futures_basis_drift",
                context_signals.futures_basis_drift,
                state.basis_history,
                state.price_history,
                state.trailing_basis_magnitude_history,
                bands["futures_basis_drift"],
            ),
        }
        raw_readings["context"] = _category_raw_readings(context_results)
        raw_readings["context"]["profile_shape"] = profile_shape.value if profile_shape else None
        raw_readings["context"]["_carry"] = _drop_none(
            {
                "futures_ltp": snapshot.futures_ltp,
                "volume_delta": raw_volume_delta,
                "spot_ltp": snapshot.spot_ltp,
                "day_high": snapshot.day_high,
                "day_low": snapshot.day_low,
                "close": snapshot.futures_ltp,
                "poc": current_value_area[0] if current_value_area else None,
                "va_low": current_value_area[1] if current_value_area else None,
                "va_high": current_value_area[2] if current_value_area else None,
            }
        )

        for category, results in (
            ("volatility", volatility_results),
            ("gamma", gamma_results),
            ("oi_structure", oi_results),
            ("order_flow", order_flow_results),
            ("context", context_results),
        ):
            for sub_signal_name, result in results.items():
                reasons[sub_signal_name] = result[3]

        category_scores = {
            "volatility": category_score([r[2] for r in volatility_results.values()]),
            "gamma": category_score([r[2] for r in gamma_results.values()]),
            "oi_structure": category_score([r[2] for r in oi_results.values()]),
            "order_flow": category_score([r[2] for r in order_flow_results.values()]),
            "context": category_score([r[2] for r in context_results.values()]),
        }
        composite = composite_score(category_scores, config.category_weights)
        proposed_state = state_for_score(composite, config.thresholds)
        pending_state, pending_streak, confirmed_state, flipped = apply_hysteresis(
            proposed_state,
            state.pending_state,
            state.pending_streak,
            state.confirmed_state,
            config.confirmation_cycles,
        )
        previous_confirmed_state = state.confirmed_state
        state.pending_state = pending_state
        state.pending_streak = pending_streak
        state.confirmed_state = confirmed_state

        now = datetime.now(timezone.utc)
        row = SignalSnapshot(
            ts=now,
            session_date=self._session_date,
            config_type=config_type,  # type: ignore[arg-type]
            dte=dte if dte is not None else -1,
            raw_readings=raw_readings,
            sub_scores=category_scores,
            composite_score=composite,
            market_state=confirmed_state,
            system_status=snapshot.system_status,
            reasons=reasons,
        )
        self._client.table(SignalSnapshot.TABLE).insert(
            row.model_dump(mode="json")
        ).execute()

        if flipped:
            trigger_categories = [
                name for name, score in category_scores.items() if abs(score - 0.5) >= 0.1
            ]
            transition = StateTransition(
                ts=now,
                from_state=previous_confirmed_state,
                to_state=confirmed_state,
                trigger_categories=trigger_categories,
                reason=explain_transition(
                    previous_confirmed_state,
                    confirmed_state,
                    trigger_categories,
                    composite,
                    config.confirmation_cycles,
                ),
            )
            self._client.table(StateTransition.TABLE).insert(
                transition.model_dump(mode="json")
            ).execute()

        state.previous_snapshot = snapshot
        self._previous_iv = current_iv

        return row


def safe_call_profile_shape(
    *args: Any,
) -> tuple[context_signals.DayProfileShape | None, tuple[Any, ...]]:
    """prior_day_profile_shape returns (shape, SignalResult) -- a different
    shape than every other sub-signal, so it needs its own safe_call variant
    rather than scorer.safe_call's single-SignalResult return assumption."""
    try:
        return context_signals.prior_day_profile_shape(*args)
    except Exception as exc:  # noqa: BLE001 -- mirrors scorer.safe_call's discipline
        band = args[-1]
        return None, (None, band, 0.5, f"prior_day_profile_shape: error ({exc.__class__.__name__})")


def _category_raw_readings(results: dict[str, tuple[Any, ...]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, (raw_value, reference_band, sub_score, reason) in results.items():
        entry: dict[str, Any] = {
            "raw_value": raw_value,
            "reference_band": list(reference_band),
            "sub_score": sub_score,
        }
        context = _extract_context(reason)
        if context:
            entry["context"] = context
        out[name] = entry
    return out


def _drop_none(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}
