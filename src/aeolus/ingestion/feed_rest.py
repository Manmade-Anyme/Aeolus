"""REST polling path (TASK-002 ADR): option chain (OI/greeks/IV per strike).

Dhan's option chain endpoint returns the full chain in one call given the
underlying's security_id + segment + expiry -- no per-strike security_id
lookup needed (unlike the futures leg on the WS path). Response field names
below follow Dhan's documented Option Chain schema; this has not yet been
exercised against a live response with real strikes, so treat the exact
nesting (`data.data.oc[strike].ce/pe.{oi,implied_volatility,greeks}`) as
provisional -- confirm on first live run and record any drift in
reports/debug/TASK-002_debug-report.md.

Expiry is sourced from Dhan's own expiry_list endpoint rather than
recomputed from the NSE holiday calendar here -- that calendar logic belongs
to whichever signal module needs DTE (out of scope for ingestion).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from dhanhq import DhanContext, dhanhq

from aeolus.ingestion.credentials import DhanCredentials
from aeolus.ingestion.models import Greeks, OptionStrike
from aeolus.ingestion.redis_client import DhanRedisClient
from aeolus.ingestion.staleness import StalenessTracker

UNDERLYING_INDEX_SEGMENT = "IDX_I"


class OptionChainPoller:
    def __init__(
        self,
        credentials: DhanCredentials,
        spot_security_id: str,
        staleness: StalenessTracker,
        hub_url: str | None = None,
    ) -> None:
        context = DhanContext(credentials.client_id, credentials.access_token)
        self._client = dhanhq(context)
        self._spot_security_id = spot_security_id
        self._staleness = staleness
        self._redis_client = DhanRedisClient(hub_url=hub_url)
        self._last_strike_count = 0
        self._short_read_streak = 0

    @property
    def degraded(self) -> bool:
        """True after 2+ consecutive polls returned fewer strikes than the
        prior cycle -- a single short read is not enough to flag STALE
        (avoids false alarms on transient partial responses)."""
        return self._short_read_streak >= 2

    def resolve_nearest_expiry(self) -> date:
        expiries_str = self._redis_client.get_expiry_list(
            symbol="NIFTY",
            underlying_scrip=int(self._spot_security_id),
            underlying_seg=UNDERLYING_INDEX_SEGMENT,
        )
        if expiries_str:
            expiries = sorted(
                datetime.strptime(d, "%Y-%m-%d").date() for d in expiries_str
            )
            today = datetime.now(timezone.utc).date()
            upcoming = [d for d in expiries if d >= today]
            if upcoming:
                return upcoming[0]

        # Fallback to direct Dhan API if Redis Hub is unavailable
        response = self._client.expiry_list(int(self._spot_security_id), UNDERLYING_INDEX_SEGMENT)
        if response["status"] != "success":
            raise RuntimeError(f"expiry_list failed: {response['remarks']}")
        expiries = sorted(
            datetime.strptime(d, "%Y-%m-%d").date() for d in response["data"]["data"]
        )
        today = datetime.now(timezone.utc).date()
        upcoming = [d for d in expiries if d >= today]
        if not upcoming:
            raise RuntimeError("No upcoming NIFTY option expiry returned by Dhan")
        return upcoming[0]

    def poll(self, expiry: date) -> list[OptionStrike]:
        chain = self._redis_client.get_option_chain(
            symbol="NIFTY",
            expiry=expiry.isoformat(),
            underlying_scrip=int(self._spot_security_id),
            underlying_seg=UNDERLYING_INDEX_SEGMENT,
        )

        if chain is None:
            # Fallback to direct Dhan API if Redis Hub is unavailable
            response = self._client.option_chain(
                int(self._spot_security_id), UNDERLYING_INDEX_SEGMENT, expiry.isoformat()
            )
            if response.get("status") != "success":
                # Connectivity/API failure -- do not touch the heartbeat, let it
                # age into STALE/DISCONNECTED via elapsed time instead.
                return []
            chain = response["data"]["data"]["oc"]

        self._staleness.touch("option_chain", datetime.now(timezone.utc))

        strikes = [_parse_strike(strike_str, legs) for strike_str, legs in chain.items()]

        if self._last_strike_count and len(strikes) < self._last_strike_count:
            self._short_read_streak += 1
        else:
            self._short_read_streak = 0
        if strikes:
            self._last_strike_count = len(strikes)

        return strikes


def _parse_strike(strike_str: str, legs: dict) -> OptionStrike:
    ce = legs.get("ce", {})
    pe = legs.get("pe", {})
    return OptionStrike(
        strike=float(strike_str),
        call_oi=int(ce.get("oi", 0)),
        put_oi=int(pe.get("oi", 0)),
        call_iv=float(ce.get("implied_volatility", 0.0)),
        put_iv=float(pe.get("implied_volatility", 0.0)),
        call_greeks=_parse_greeks(ce.get("greeks", {})),
        put_greeks=_parse_greeks(pe.get("greeks", {})),
    )


def _parse_greeks(raw: dict) -> Greeks:
    return Greeks(
        delta=float(raw.get("delta", 0.0)),
        gamma=float(raw.get("gamma", 0.0)),
        theta=float(raw.get("theta", 0.0)),
        vega=float(raw.get("vega", 0.0)),
    )
