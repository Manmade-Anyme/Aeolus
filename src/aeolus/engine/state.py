"""EngineState (TASK-008 ADR) -- the seeding/persistence contract TASK-003..007
all deferred to this module. Session-scoped fields are cleared explicitly by
Engine.end_session() at market close (never lazily inferred). Cross-session
fields are seeded once at Engine.start() from prior signal_snapshots rows.

Nothing here does I/O itself except the `load` classmethod -- everything else
is a plain in-memory container the engine mutates each cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from supabase import Client

from aeolus.ingestion.models import IngestionSnapshot
from aeolus.storage.models import MarketState, SignalSnapshot

MAX_TRAILING_SESSIONS = 60


def _carry(row: dict, category: str) -> dict:
    return row.get("raw_readings", {}).get(category, {}).get("_carry", {}) or {}


@dataclass
class EngineState:
    # --- session-scoped: cleared by Engine.end_session(), never lazily ---
    previous_snapshot: IngestionSnapshot | None = None
    cvd_delta_history: list[float] = field(default_factory=list)
    price_history: list[float] = field(default_factory=list)
    basis_history: list[float] = field(default_factory=list)
    established_range: tuple[float, float] | None = None
    cycle_price_volume_history: list[tuple[float, float]] = field(default_factory=list)
    session_open: float | None = None
    session_reference_price: float | None = None
    session_open_max_pain: float | None = None
    pending_state: MarketState = "NO_GO"
    pending_streak: int = 0
    confirmed_state: MarketState = "NO_GO"

    # --- cross-session: seeded once at start(), untouched by end_session() ---
    trailing_iv_history: list[float] = field(default_factory=list)
    trailing_spot_history: list[float] = field(default_factory=list)
    trailing_vix_history: list[float] = field(default_factory=list)
    trailing_iv_rising_magnitude_history: list[float] = field(default_factory=list)
    trailing_iv_falling_magnitude_history: list[float] = field(default_factory=list)
    trailing_gex_magnitude_history: list[float] = field(default_factory=list)
    trailing_pcr_roc_magnitude_history: list[float] = field(default_factory=list)
    trailing_wall_proximity_history: list[float] = field(default_factory=list)
    trailing_wall_strength_trend_history: list[float] = field(default_factory=list)
    trailing_max_pain_drift_history: list[float] = field(default_factory=list)
    trailing_cvd_magnitude_history: list[float] = field(default_factory=list)
    trailing_imbalance_magnitude_history: list[float] = field(default_factory=list)
    trailing_excursion_history: list[float] = field(default_factory=list)
    trailing_average_range_history: list[float] = field(default_factory=list)
    trailing_gap_magnitude_history: list[float] = field(default_factory=list)
    trailing_basis_magnitude_history: list[float] = field(default_factory=list)
    average_daily_volume: float | None = None
    prior_day_high: float | None = None
    prior_day_low: float | None = None
    prior_close: float | None = None
    prior_value_area: tuple[float, float, float] | None = None

    def clear_session_scoped(self) -> None:
        """Engine.end_session() -- see TASK-008 ADR Decision §7. Never touches
        Supabase; cross-session fields below this line are untouched."""
        self.previous_snapshot = None
        self.cvd_delta_history = []
        self.price_history = []
        self.basis_history = []
        self.established_range = None
        self.cycle_price_volume_history = []
        self.session_open = None
        self.session_reference_price = None
        self.session_open_max_pain = None
        self.pending_streak = 0

    @classmethod
    def load(cls, client: Client, session_date: date) -> EngineState:
        """Seeds cross-session trailing histories + prior-day context from the
        most recent prior session_dates' final rows, and (on a same-day
        restart) rebuilds today's session-scoped state from today's own rows.

        previous_snapshot always starts None after a restart -- option_chain
        is never persisted to signal_snapshots by design, so the true previous
        IngestionSnapshot isn't recoverable. This costs exactly one cycle's
        worth of TASK-005 functions degrading to their normal insufficient-
        data path before resuming -- an accepted limitation, not a bug.
        """
        state = cls()

        prior_rows = (
            client.table(SignalSnapshot.TABLE)
            .select("session_date, ts, raw_readings, sub_scores, composite_score, market_state")
            .lt("session_date", session_date.isoformat())
            .order("ts", desc=True)
            .limit(MAX_TRAILING_SESSIONS * 20)
            .execute()
            .data
        )
        state._seed_cross_session(prior_rows)  # type: ignore[arg-type]

        today_rows = (
            client.table(SignalSnapshot.TABLE)
            .select("ts, raw_readings, market_state")
            .eq("session_date", session_date.isoformat())
            .order("ts")
            .execute()
            .data
        )
        if today_rows:
            state._seed_same_day_restart(today_rows)  # type: ignore[arg-type]

        return state

    def _seed_cross_session(self, prior_rows: list[dict]) -> None:
        """prior_rows is ordered most-recent-first, possibly many rows per
        day. Keep only the last (chronologically final) row per session_date,
        most recent MAX_TRAILING_SESSIONS distinct dates."""
        last_row_per_date: dict[str, dict] = {}
        for row in prior_rows:
            last_row_per_date.setdefault(row["session_date"], row)  # first-seen = most recent (desc order)

        ordered_dates = sorted(last_row_per_date, reverse=True)[:MAX_TRAILING_SESSIONS]
        if not ordered_dates:
            return

        daily_rows = [last_row_per_date[d] for d in reversed(ordered_dates)]  # oldest -> newest

        volumes: list[float] = []
        for row in daily_rows:
            vol_carry = _carry(row, "volatility")
            if "current_iv" in vol_carry:
                self.trailing_iv_history.append(vol_carry["current_iv"])
            if "spot_ltp" in vol_carry:
                self.trailing_spot_history.append(vol_carry["spot_ltp"])
            if "india_vix" in vol_carry:
                self.trailing_vix_history.append(vol_carry["india_vix"])

            gamma_reading = row.get("raw_readings", {}).get("gamma", {}).get("gex_regime", {})
            if gamma_reading.get("raw_value") is not None:
                self.trailing_gex_magnitude_history.append(abs(gamma_reading["raw_value"]))

            oi_readings = row.get("raw_readings", {}).get("oi_structure", {})
            pcr = oi_readings.get("pcr_level_and_roc", {})
            if pcr.get("raw_value") is not None:
                self.trailing_pcr_roc_magnitude_history.append(abs(pcr["raw_value"]))
            wall = oi_readings.get("oi_wall_proximity_and_strength", {})
            if wall.get("raw_value") is not None:
                self.trailing_wall_proximity_history.append(abs(wall["raw_value"]))
            strength_trend = wall.get("context", {}).get("strength_trend")
            if strength_trend is not None:
                self.trailing_wall_strength_trend_history.append(strength_trend)
            max_pain = oi_readings.get("max_pain_drift", {})
            if max_pain.get("raw_value") is not None:
                self.trailing_max_pain_drift_history.append(abs(max_pain["raw_value"]))

            of_readings = row.get("raw_readings", {}).get("order_flow", {})
            cvd = of_readings.get("cvd_direction_and_divergence", {})
            if cvd.get("raw_value") is not None:
                self.trailing_cvd_magnitude_history.append(abs(cvd["raw_value"]))
            imbalance = of_readings.get("delta_imbalance_and_absorption", {})
            if imbalance.get("raw_value") is not None:
                self.trailing_imbalance_magnitude_history.append(abs(imbalance["raw_value"]))
            vpr = of_readings.get("volume_participation_range", {})
            if vpr.get("raw_value") is not None:
                self.trailing_excursion_history.append(abs(vpr["raw_value"]))
            of_carry = _carry(row, "order_flow")
            if "volume" in of_carry:
                volumes.append(of_carry["volume"])

            ctx_readings = row.get("raw_readings", {}).get("context", {})
            profile = ctx_readings.get("prior_day_profile_shape", {})
            if profile.get("raw_value") is not None:
                self.trailing_average_range_history.append(abs(profile["raw_value"]))
            gap = ctx_readings.get("gap_classification", {})
            if gap.get("raw_value") is not None:
                self.trailing_gap_magnitude_history.append(abs(gap["raw_value"]))
            basis_drift = ctx_readings.get("futures_basis_drift", {})
            if basis_drift.get("raw_value") is not None:
                self.trailing_basis_magnitude_history.append(abs(basis_drift["raw_value"]))

        # IV trend magnitude histories need the day-over-day IV *change*, not level
        for earlier, later in zip(self.trailing_iv_history, self.trailing_iv_history[1:]):
            delta = later - earlier
            if delta > 0:
                self.trailing_iv_rising_magnitude_history.append(delta)
            elif delta < 0:
                self.trailing_iv_falling_magnitude_history.append(abs(delta))

        if volumes:
            self.average_daily_volume = sum(volumes) / len(volumes)

        # Most recent prior day only -- "yesterday's completed profile", not a rolling window
        most_recent = daily_rows[-1]
        most_recent_carry = _carry(most_recent, "context")
        self.prior_day_high = most_recent_carry.get("day_high")
        self.prior_day_low = most_recent_carry.get("day_low")
        self.prior_close = most_recent_carry.get("close")
        poc, va_low, va_high = (
            most_recent_carry.get("poc"),
            most_recent_carry.get("va_low"),
            most_recent_carry.get("va_high"),
        )
        if poc is not None and va_low is not None and va_high is not None:
            self.prior_value_area = (poc, va_low, va_high)

    def _seed_same_day_restart(self, today_rows: list[dict]) -> None:
        """today_rows is ordered chronologically (oldest first)."""
        for row in today_rows:
            of_carry = _carry(row, "order_flow")
            if "cvd_delta" in of_carry:
                self.cvd_delta_history.append(of_carry["cvd_delta"])
            if "futures_ltp" in of_carry:
                self.price_history.append(of_carry["futures_ltp"])
            if "futures_basis" in of_carry:
                self.basis_history.append(of_carry["futures_basis"])
            if of_carry.get("established_range_low") is not None:
                self.established_range = (
                    of_carry["established_range_low"],
                    of_carry["established_range_high"],
                )

            ctx_carry = _carry(row, "context")
            if "futures_ltp" in ctx_carry and "volume_delta" in ctx_carry:
                self.cycle_price_volume_history.append(
                    (ctx_carry["futures_ltp"], ctx_carry["volume_delta"])
                )
            if self.session_open is None and "futures_ltp" in ctx_carry:
                self.session_open = ctx_carry["futures_ltp"]
            if self.session_reference_price is None and "spot_ltp" in ctx_carry:
                self.session_reference_price = ctx_carry["spot_ltp"]

            oi_carry = _carry(row, "oi_structure")
            if self.session_open_max_pain is None and "max_pain" in oi_carry:
                self.session_open_max_pain = oi_carry["max_pain"]

        self.confirmed_state = today_rows[-1]["market_state"]
        self.pending_state = self.confirmed_state
        self.pending_streak = 0
