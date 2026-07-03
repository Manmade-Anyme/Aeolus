"""IngestionService (TASK-002 ADR): wires credentials + instruments + WS + REST
+ staleness into the single public entrypoint used by the scheduler
(TASK-013) and, transitively, every signal module.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from aeolus.ingestion.credentials import CredentialsSource
from aeolus.ingestion.feed_rest import OptionChainPoller
from aeolus.ingestion.feed_ws import LiveFeed
from aeolus.ingestion.instruments import InstrumentResolver
from aeolus.ingestion.models import IngestionSnapshot
from aeolus.ingestion.staleness import StalenessTracker, aggregate_status


class IngestionService:
    def __init__(self, supabase_url: str, supabase_key: str) -> None:
        self._credentials_source = CredentialsSource(supabase_url, supabase_key)
        self._instruments = InstrumentResolver()
        self._staleness = StalenessTracker()
        self._live_feed: LiveFeed | None = None
        self._option_poller: OptionChainPoller | None = None
        self._option_expiry: date | None = None
        self.lot_size: int | None = None

    def start(self) -> None:
        """Authenticates, resolves instruments, opens WS, ready for polling."""
        credentials = self._credentials_source.load()
        self._instruments.refresh()

        spot_id = self._instruments.resolve_spot()
        vix_id = self._instruments.resolve_vix()
        futures = self._instruments.resolve_current_month_futures(
            datetime.now(timezone.utc).date()
        )
        self.lot_size = futures.lot_size

        self._live_feed = LiveFeed(
            credentials, spot_id, futures.security_id, vix_id, self._staleness
        )
        self._live_feed.start()

        self._option_poller = OptionChainPoller(credentials, spot_id, self._staleness)
        self._option_expiry = self._option_poller.resolve_nearest_expiry()

    def latest(self) -> IngestionSnapshot:
        """Returns current snapshot. Never raises on stale/disconnected feeds
        -- that state is surfaced via system_status, not exceptions."""
        if self._live_feed is None or self._option_poller is None or self._option_expiry is None:
            raise RuntimeError("IngestionService.start() must be called first")

        now = datetime.now(timezone.utc)

        spot_ltp = self._live_feed.latest_spot_ltp()
        futures_ltp = self._live_feed.latest_futures_ltp()
        futures_basis = (
            futures_ltp - spot_ltp if spot_ltp is not None and futures_ltp is not None else None
        )

        option_chain = self._option_poller.poll(self._option_expiry)

        detail = self._staleness.detail(now)
        if self._option_poller.degraded and detail.get("option_chain") == "OK":
            detail["option_chain"] = "STALE"

        return IngestionSnapshot(
            ts=now,
            spot_ltp=spot_ltp,
            futures_ltp=futures_ltp,
            futures_basis=futures_basis,
            depth=self._live_feed.latest_depth(),
            option_chain=option_chain,
            india_vix=self._live_feed.latest_vix_ltp(),
            gift_nifty=None,
            volume=self._live_feed.latest_volume(),
            total_buy_quantity=self._live_feed.latest_total_buy_quantity(),
            total_sell_quantity=self._live_feed.latest_total_sell_quantity(),
            day_high=self._live_feed.latest_day_high(),
            day_low=self._live_feed.latest_day_low(),
            expiry_date=self._option_expiry,
            system_status=aggregate_status(detail),
            system_status_detail=detail,
        )

    def stop(self) -> None:
        if self._live_feed:
            self._live_feed.stop()
