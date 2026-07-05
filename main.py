#!/usr/bin/env python
"""AEOLUS entrypoint — runs one trading session, then exits (for Fly.io scale-to-0)."""

import logging
import os
import sys

from aeolus.ml.config import MLTuning
from aeolus.ml.hooks import MLHooks
from aeolus.jobs.retention import RetentionJob
from aeolus.scheduler.scheduler import Scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("aeolus.main")


def main() -> None:
    """Load config from env, wire dependencies, run one session."""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY")
    market_webhook_url = os.getenv("MARKET_DISCORD_WEBHOOK_URL")
    status_webhook_url = os.getenv("STATUS_DISCORD_WEBHOOK_URL")
    ml_webhook_url = os.getenv("ML_DISCORD_WEBHOOK_URL")

    if not all([supabase_url, supabase_key, market_webhook_url, status_webhook_url]):
        logger.error("Missing required env vars: SUPABASE_URL, SUPABASE_ANON_KEY, MARKET_DISCORD_WEBHOOK_URL, STATUS_DISCORD_WEBHOOK_URL")
        sys.exit(1)

    logger.info("Starting AEOLUS session")

    # Wire ML overlay (optional)
    # FIX: previously passed ml_webhook_url as supabase_url positional arg
    # (MLHooks(supabase_url) signature), which would silently use a Discord
    # webhook URL as the Supabase client endpoint. Now passes supabase_url +
    # supabase_key properly and routes ml_webhook_url through MLTuning.
    ml_hooks = None
    if ml_webhook_url:
        ml_hooks = MLHooks(
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            tuning=MLTuning(ml_discord_webhook_url=ml_webhook_url),
            client=None,
        )
        logger.info("ML overlay enabled")
    else:
        logger.info("ML overlay disabled (no ML_DISCORD_WEBHOOK_URL)")

    # Wire retention job
    retention_job = RetentionJob(supabase_url, supabase_key)

    # Instantiate and run
    scheduler = Scheduler(
        supabase_url=supabase_url,
        supabase_key=supabase_key,
        market_webhook_url=market_webhook_url,
        status_webhook_url=status_webhook_url,
        ml_hooks=ml_hooks,
        retention_job=retention_job,
    )

    try:
        scheduler.run()
        logger.info("Session complete, exiting (Fly machine will scale to 0)")
    except KeyboardInterrupt:
        logger.info("Interrupted, exiting")
        sys.exit(0)
    except Exception:
        logger.exception("Session failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
