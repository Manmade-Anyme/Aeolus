#!/usr/bin/env python
"""Fire all 4 ML Discord message types to verify output formatting.
Run: python scripts/test_ml_discord.py

No Supabase or market data needed -- pure MLDiscordDispatcher unit-test."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

from datetime import datetime, timezone
from uuid import uuid4

from aeolus.ml.output import MLDiscordDispatcher
from aeolus.ml.scorer import ScoreEvent
from aeolus.storage.models import SignalSnapshot

WEBHOOK_URL = os.getenv("ML_DISCORD_WEBHOOK_URL")
if not WEBHOOK_URL:
    sys.exit("ML_DISCORD_WEBHOOK_URL not set in .env")

d = MLDiscordDispatcher(WEBHOOK_URL)

# 1 — warm-up progress
print("1/4 posting warm-up progress...")
d.post_warmup_progress("NON_EXPIRY", day=7, target=15)

# 2 — anomaly flagged (need proper ScoreEvent + SignalSnapshot stubs)
print("2/4 posting anomaly flagged...")
event = ScoreEvent(
    kind="ANOMALY_ENTER",
    score=0.721,
    z_by_feature={"gex_regime": 2.1, "iv_percentile_rank": 1.8, "pcr_level_and_roc": 1.4},
    model_version_id=uuid4(),
)
snap = SignalSnapshot(
    id=uuid4(),
    ts=datetime.now(timezone.utc),
    session_date=datetime.now(timezone.utc).date(),
    config_type="NON_EXPIRY",
    dte=1,
    system_status="OK",
    raw_readings={},
    sub_scores={},
    composite_score=0.5,
    market_state="NO_GO",
    reasons={},
)
d.post_anomaly(
    event,
    "anomalous — driven by gex_regime (+2.1σ), iv_percentile_rank (+1.8σ), pcr_level_and_roc (+1.4σ) | score 0.721 vs flag 0.680 | model v1",
    snap,
)

# 3 — anomaly cleared
print("3/4 posting anomaly cleared...")
d.post_clear(event, "cleared — score 0.510 vs clear 0.620 | model v1", "NON_EXPIRY")

# 4 — model go-live
print("4/4 posting go-live...")
d.post_golive("NON_EXPIRY", model_version=1)

print("done — check Discord #ml channel")
