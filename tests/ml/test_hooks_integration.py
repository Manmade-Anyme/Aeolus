"""Live integration test for MLHooks.on_cycle's full path (TASK-021 ADR
Definition of Done): a real Supabase-backed FeatureStore + LiveScorer with a
real fitted IsolationForest (same toy-model technique as
test_scorer_integration.py) score a spike snapshot and the resulting advisory
is posted through MLDiscordDispatcher, whose HTTP boundary is mocked (not
internal). Proves the full append -> score -> explain -> post chain wires
together correctly, not just each piece in isolation.
"""

import base64
import io
import json
from datetime import date, datetime, timezone
from uuid import uuid4

import httpx
import joblib
import numpy as np
import pytest
import sklearn
from dotenv import dotenv_values
from sklearn.ensemble import IsolationForest

from aeolus.ml.features import FEATURE_ORDER, FEATURE_SET_VERSION
from aeolus.ml.hooks import MLHooks
from aeolus.ml.output import MLDiscordDispatcher
from aeolus.storage.models import SignalSnapshot

ENV_PATH = __file__.rsplit("/tests/", 1)[0] + "/.env"
_cfg = dotenv_values(ENV_PATH)
SUPABASE_URL = _cfg.get("SUPABASE_URL")
SUPABASE_KEY = _cfg.get("SUPABASE_KEY")

pytestmark = pytest.mark.skipif(
    not (SUPABASE_URL and SUPABASE_KEY),
    reason="SUPABASE_URL/SUPABASE_KEY not set in .env",
)

if SUPABASE_URL and SUPABASE_KEY:
    from supabase import create_client

SESSION_DATE = date(2030, 8, 1)
BASE_TS = datetime(2030, 8, 1, 4, 0, tzinfo=timezone.utc)


def _snapshot_from_features(values: dict[str, float], *, config_type: str, ts: datetime) -> SignalSnapshot:
    raw_readings = {
        "volatility": {
            "iv_percentile_rank": {"raw_value": values["iv_percentile_rank"], "reference_band": [0.0, 1.0], "sub_score": 0.5},
            "vix_level_and_roc": {
                "raw_value": values["vix_level"],
                "reference_band": [0.0, 1.0],
                "sub_score": 0.5,
                "context": {"roc": values["vix_roc"]},
            },
            "expected_move_consumed_ratio": {
                "raw_value": values["expected_move_consumed_ratio"],
                "reference_band": [0.0, 1.0],
                "sub_score": 0.5,
            },
        },
        "gamma": {
            "gex_regime": {"raw_value": values["gex_magnitude"], "reference_band": [0.0, 1.0], "sub_score": 0.5},
            "spot_distance_from_flip": {
                "raw_value": values["spot_distance_from_flip"],
                "reference_band": [0.0, 1.0],
                "sub_score": 0.5,
            },
        },
        "oi_structure": {
            "pcr_level_and_roc": {
                "raw_value": values["pcr_roc"],
                "reference_band": [0.0, 1.0],
                "sub_score": 0.5,
                "context": {"pcr_level": values["pcr_level"]},
            },
        },
        "order_flow": {
            "cvd_direction_and_divergence": {
                "raw_value": values["cvd_divergence"],
                "reference_band": [0.0, 1.0],
                "sub_score": 0.5,
            },
        },
        "context": {},
    }
    return SignalSnapshot(
        id=uuid4(),
        ts=ts,
        session_date=ts.date(),
        config_type=config_type,  # type: ignore[arg-type]
        dte=3,
        raw_readings=raw_readings,
        sub_scores={
            "volatility": values["sub_score_volatility"],
            "gamma": values["sub_score_gamma"],
            "oi_structure": values["sub_score_oi_structure"],
            "order_flow": values["sub_score_order_flow"],
            "context": values["sub_score_context"],
        },
        composite_score=values["composite_score"],
        market_state="PREPARE",
        system_status="OK",  # type: ignore[arg-type]
        reasons={},
    )


@pytest.fixture(scope="module")
def toy_model():
    rng = np.random.default_rng(11)
    matrix = rng.normal(size=(300, len(FEATURE_ORDER)))
    matrix[:, FEATURE_ORDER.index("gex_magnitude")] = np.abs(matrix[:, FEATURE_ORDER.index("gex_magnitude")])
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    standardized = (matrix - mean) / std

    model = IsolationForest(n_estimators=50, max_samples="auto", random_state=11)
    model.fit(standardized)
    scores = -model.score_samples(standardized)

    flag_threshold = float(np.quantile(scores, 0.95))
    clear_threshold = float(np.quantile(scores, 0.90))
    spike_idx = int(np.argmax(scores))

    buffer = io.BytesIO()
    joblib.dump(model, buffer)
    model_blob = base64.b64encode(buffer.getvalue()).decode("ascii")

    return {
        "matrix": matrix,
        "mean": mean,
        "std": std,
        "flag_threshold": flag_threshold,
        "clear_threshold": clear_threshold,
        "spike_idx": spike_idx,
        "model_blob": model_blob,
    }


def _row_values(toy_model, idx: int) -> dict[str, float]:
    return dict(zip(FEATURE_ORDER, toy_model["matrix"][idx].tolist(), strict=True))


@pytest.fixture
def client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _insert_registry_row(client, config_type: str, toy_model) -> str:
    row_id = str(uuid4())
    row = {
        "id": row_id,
        "config_type": config_type,
        "version": 4,
        "feature_set_version": FEATURE_SET_VERSION,
        "model_blob": toy_model["model_blob"],
        "sklearn_version": sklearn.__version__,
        "scaler_mean": dict(zip(FEATURE_ORDER, toy_model["mean"].tolist(), strict=True)),
        "scaler_std": dict(zip(FEATURE_ORDER, toy_model["std"].tolist(), strict=True)),
        "flag_threshold": toy_model["flag_threshold"],
        "clear_threshold": toy_model["clear_threshold"],
        "window_start": date(2030, 6, 1).isoformat(),
        "window_end": date(2030, 6, 30).isoformat(),
        "sample_count": 300,
        "trading_day_count": 20,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    client.table("ml_model_registry").insert(row).execute()
    return row_id


class _Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, headers = self.responses.pop(0)
        return httpx.Response(status, headers=headers or {}, request=request)


def _cleanup(client, registry_id: str, config_type: str, session_date: date) -> None:
    try:
        client.table("ml_model_registry").delete().eq("id", registry_id).execute()
    finally:
        try:
            client.table("ml_anomaly_scores").delete().eq("session_date", session_date.isoformat()).eq(
                "config_type", config_type
            ).execute()
        finally:
            client.table("ml_feature_store").delete().eq("session_date", session_date.isoformat()).execute()


def test_on_cycle_spike_appends_scores_and_posts_one_advisory(client, toy_model):
    registry_id = _insert_registry_row(client, "NON_EXPIRY", toy_model)
    recorder = _Recorder([(204, None)])
    dispatcher = MLDiscordDispatcher("https://discord.test/ml", transport=httpx.MockTransport(recorder))
    hooks = MLHooks("http://fake", "fake-key", client=client, dispatcher=dispatcher)
    hooks.start_session(SESSION_DATE)

    try:
        spike = _snapshot_from_features(_row_values(toy_model, toy_model["spike_idx"]), config_type="NON_EXPIRY", ts=BASE_TS)

        hooks.on_cycle(spike)

        # Feature-store append landed.
        stored = (
            client.table("ml_feature_store")
            .select("id")
            .eq("source_snapshot_id", str(spike.id))
            .execute()
            .data
        )
        assert len(stored) == 1

        # Scorer wrote a flagged row and MLHooks posted exactly one advisory.
        score_rows = (
            client.table("ml_anomaly_scores")
            .select("flagged")
            .eq("session_date", SESSION_DATE.isoformat())
            .eq("config_type", "NON_EXPIRY")
            .execute()
            .data
        )
        assert len(score_rows) == 1
        assert score_rows[0]["flagged"] is True

        assert len(recorder.requests) == 1
        body = json.loads(recorder.requests[0].content.decode())
        embed = body["embeds"][0]
        assert "\U0001f52c ML" in embed["title"]
        assert "model v4" in embed["description"]
        assert embed["footer"]["text"] == "advisory only — does not change engine state"
    finally:
        _cleanup(client, registry_id, "NON_EXPIRY", SESSION_DATE)
