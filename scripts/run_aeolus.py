"""Process entrypoint (TASK-013 ADR). What Fly's cron-scaled machine
actually invokes: reads env vars, runs one bounded trading session, exits.
Deployment (fly.toml, cron scheduling) is deferred -- docs/OPEN_DECISIONS.md #5.
"""

from __future__ import annotations

import logging
import os
import sys

from aeolus.scheduler.scheduler import Scheduler


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        supabase_url = os.environ["SUPABASE_URL"]
        supabase_key = os.environ["SUPABASE_KEY"]
        market_webhook_url = os.environ["DISCORD_MARKET_WEBHOOK_URL"]
        status_webhook_url = os.environ["DISCORD_STATUS_WEBHOOK_URL"]
    except KeyError as exc:
        print(f"missing required env var: {exc}", file=sys.stderr)
        return 1

    scheduler = Scheduler(supabase_url, supabase_key, market_webhook_url, status_webhook_url)
    scheduler.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
