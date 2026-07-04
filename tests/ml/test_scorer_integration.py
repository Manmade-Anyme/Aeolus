"""Live integration tests for LiveScorer (TASK-018 ADR) -- real Supabase, no
mocking of internals: a real IsolationForest is fitted (small toy window,
same fit/threshold formula as ModelTrainer) and inserted as a real
ml_model_registry row; snapshots are constructed so extract_features()
returns exactly the raw feature vectors used to fit/score the toy model,
giving full control over which cycles are "normal" vs "spike" vs "hover"
without mocking any scorer internals. Far-off dates, self-cleaning.
"""

import base64
import io
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import joblib
import numpy as np
import pytest
import sklearn
from dotenv import dotenv_values
from sklearn.ensemble import IsolationForest

from aeolus.ml.features import FEATURE_ORDER, FEATURE_SET_VERSION
from aeolus.ml.scorer import LiveScorer
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

SESSION_DATE = date(2030, 7, 1)
BASE_TS = datetime(2030, 7, 1, 4, 0, tzinfo=timezone.utc)


def _snapshot_from_features(values: dict[str, float], *, config_type: str, ts: datetime) -> SignalSnapshot:
    """Builds a SignalSnapshot whose extract_features() output is exactly
    `values` (keyed by FEATURE_ORDER) -- see aeolus.ml.features accessor
    mapping: 5 category sub-scores, composite_score, and 9 raw-reading
    leaves (2 of them nested under a sub-signal's context dict)."""
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
    """One real fitted IsolationForest + scaler + thresholds, same formula as
    ModelTrainer, small enough to be deterministic and fast. Returns the raw
    matrix, thresholds, and the row indices for a calm/spike/hover point so
    tests can pick known-outcome cycles without touching scorer internals."""
    rng = np.random.default_rng(7)
    matrix = rng.normal(size=(300, len(FEATURE_ORDER)))
    # gex_magnitude is abs(gex_regime.raw_value) in extract_features (aeolus.ml.features)
    # -- force this column non-negative so a snapshot built from any row round-trips
    # through extract_features() back to the exact value used to fit/score here.
    matrix[:, FEATURE_ORDER.index("gex_magnitude")] = np.abs(matrix[:, FEATURE_ORDER.index("gex_magnitude")])
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    standardized = (matrix - mean) / std

    model = IsolationForest(n_estimators=50, max_samples="auto", random_state=7)
    model.fit(standardized)
    scores = -model.score_samples(standardized)

    flag_threshold = float(np.quantile(scores, 0.95))
    clear_threshold = float(np.quantile(scores, 0.90))

    spike_idx = int(np.argmax(scores))
    calm_idx = int(np.argmin(scores))
    hover_idx = next(
        i for i, s in enumerate(scores) if clear_threshold <= s < flag_threshold and i not in (spike_idx, calm_idx)
    )

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
        "calm_idx": calm_idx,
        "hover_idx": hover_idx,
        "model_blob": model_blob,
    }


def _row_values(toy_model, idx: int) -> dict[str, float]:
    return dict(zip(FEATURE_ORDER, toy_model["matrix"][idx].tolist(), strict=True))


@pytest.fixture
def client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _insert_registry_row(
    client, config_type: str, toy_model, *, sklearn_version: str | None = None, feature_set_version: int | None = None
) -> str:
    row_id = str(uuid4())
    row = {
        "id": row_id,
        "config_type": config_type,
        "version": 1,
        "feature_set_version": feature_set_version if feature_set_version is not None else FEATURE_SET_VERSION,
        "model_blob": toy_model["model_blob"],
        "sklearn_version": sklearn_version or sklearn.__version__,
        "scaler_mean": dict(zip(FEATURE_ORDER, toy_model["mean"].tolist(), strict=True)),
        "scaler_std": dict(zip(FEATURE_ORDER, toy_model["std"].tolist(), strict=True)),
        "flag_threshold": toy_model["flag_threshold"],
        "clear_threshold": toy_model["clear_threshold"],
        "window_start": date(2030, 5, 1).isoformat(),
        "window_end": date(2030, 5, 31).isoformat(),
        "sample_count": 300,
        "trading_day_count": 20,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    client.table("ml_model_registry").insert(row).execute()
    return row_id


def _cleanup(client, registry_id: str | None, config_type: str, session_date: date) -> None:
    if registry_id:
        client.table("ml_model_registry").delete().eq("id", registry_id).execute()
    client.table("ml_anomaly_scores").delete().eq("session_date", session_date.isoformat()).eq(
        "config_type", config_type
    ).execute()


def test_normal_spike_hover_clear_sequence(client, toy_model):
    registry_id = _insert_registry_row(client, "NON_EXPIRY", toy_model)
    scorer = LiveScorer(SUPABASE_URL, SUPABASE_KEY, client=client)
    scorer.start_session(SESSION_DATE)

    try:
        calm = _snapshot_from_features(_row_values(toy_model, toy_model["calm_idx"]), config_type="NON_EXPIRY", ts=BASE_TS)
        spike = _snapshot_from_features(
            _row_values(toy_model, toy_model["spike_idx"]), config_type="NON_EXPIRY", ts=BASE_TS + timedelta(minutes=5)
        )
        hover = _snapshot_from_features(
            _row_values(toy_model, toy_model["hover_idx"]), config_type="NON_EXPIRY", ts=BASE_TS + timedelta(minutes=10)
        )

        assert scorer.score_cycle(calm) is None  # normal stream -> zero events

        enter_event = scorer.score_cycle(spike)
        assert enter_event is not None
        assert enter_event.kind == "ANOMALY_ENTER"
        assert enter_event.model_version_id == registry_id or str(enter_event.model_version_id) == registry_id

        assert scorer.score_cycle(spike) is None  # debounce: still anomalous, no re-fire
        assert scorer.score_cycle(hover) is None  # hover between thresholds -> hold, no flap

        clear_event = scorer.score_cycle(calm)
        assert clear_event is not None
        assert clear_event.kind == "ANOMALY_CLEAR"

        rows = (
            client.table("ml_anomaly_scores")
            .select("*")
            .eq("session_date", SESSION_DATE.isoformat())
            .eq("config_type", "NON_EXPIRY")
            .order("ts")
            .execute()
            .data
        )
        assert len(rows) == 5
        assert [row["flagged"] for row in rows] == [False, True, True, True, False]
        assert rows[1]["top_features"] is not None  # entry row has attribution
        assert rows[0]["top_features"] is None  # non-transition rows don't
        assert rows[4]["top_features"] is not None  # clear row has attribution
    finally:
        _cleanup(client, registry_id, "NON_EXPIRY", SESSION_DATE)


def test_stale_snapshot_produces_no_row_no_event(client, toy_model):
    registry_id = _insert_registry_row(client, "EXPIRY", toy_model)
    scorer = LiveScorer(SUPABASE_URL, SUPABASE_KEY, client=client)
    scorer.start_session(SESSION_DATE)

    try:
        snapshot = _snapshot_from_features(_row_values(toy_model, toy_model["spike_idx"]), config_type="EXPIRY", ts=BASE_TS)
        snapshot = snapshot.model_copy(update={"system_status": "STALE"})

        assert scorer.score_cycle(snapshot) is None

        rows = client.table("ml_anomaly_scores").select("id").eq("session_date", SESSION_DATE.isoformat()).eq(
            "config_type", "EXPIRY"
        ).execute().data
        assert rows == []
    finally:
        _cleanup(client, registry_id, "EXPIRY", SESSION_DATE)


def test_missing_feature_produces_no_row_no_event(client, toy_model):
    registry_id = _insert_registry_row(client, "EXPIRY", toy_model)
    scorer = LiveScorer(SUPABASE_URL, SUPABASE_KEY, client=client)
    scorer.start_session(SESSION_DATE)

    try:
        snapshot = _snapshot_from_features(_row_values(toy_model, toy_model["spike_idx"]), config_type="EXPIRY", ts=BASE_TS)
        broken_readings = dict(snapshot.raw_readings)
        broken_readings["gamma"] = {
            **broken_readings["gamma"],
            "gex_regime": {"raw_value": None, "reference_band": [0.0, 1.0], "sub_score": 0.5},
        }
        snapshot = snapshot.model_copy(update={"raw_readings": broken_readings})

        assert scorer.score_cycle(snapshot) is None

        rows = client.table("ml_anomaly_scores").select("id").eq("session_date", SESSION_DATE.isoformat()).eq(
            "config_type", "EXPIRY"
        ).execute().data
        assert rows == []
    finally:
        _cleanup(client, registry_id, "EXPIRY", SESSION_DATE)


def test_no_registry_row_produces_no_row_no_event(client, toy_model):
    scorer = LiveScorer(SUPABASE_URL, SUPABASE_KEY, client=client)
    scorer.start_session(SESSION_DATE)

    snapshot = _snapshot_from_features(_row_values(toy_model, toy_model["spike_idx"]), config_type="EXPIRY", ts=BASE_TS)
    assert scorer.score_cycle(snapshot) is None


def test_sklearn_version_mismatch_treated_as_no_model(client, toy_model, caplog):
    registry_id = _insert_registry_row(client, "EXPIRY", toy_model, sklearn_version="0.1.0")
    scorer = LiveScorer(SUPABASE_URL, SUPABASE_KEY, client=client)
    scorer.start_session(SESSION_DATE)

    try:
        snapshot = _snapshot_from_features(_row_values(toy_model, toy_model["spike_idx"]), config_type="EXPIRY", ts=BASE_TS)
        with caplog.at_level("WARNING"):
            assert scorer.score_cycle(snapshot) is None
        assert any("mismatch" in record.message for record in caplog.records)

        rows = client.table("ml_anomaly_scores").select("id").eq("session_date", SESSION_DATE.isoformat()).eq(
            "config_type", "EXPIRY"
        ).execute().data
        assert rows == []
    finally:
        _cleanup(client, registry_id, "EXPIRY", SESSION_DATE)


def test_feature_set_version_mismatch_treated_as_no_model(client, toy_model):
    registry_id = _insert_registry_row(client, "EXPIRY", toy_model, feature_set_version=999)
    scorer = LiveScorer(SUPABASE_URL, SUPABASE_KEY, client=client)
    scorer.start_session(SESSION_DATE)

    try:
        snapshot = _snapshot_from_features(_row_values(toy_model, toy_model["spike_idx"]), config_type="EXPIRY", ts=BASE_TS)
        assert scorer.score_cycle(snapshot) is None
    finally:
        _cleanup(client, registry_id, "EXPIRY", SESSION_DATE)
