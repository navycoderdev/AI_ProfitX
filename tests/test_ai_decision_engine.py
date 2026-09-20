from datetime import datetime, timedelta, timezone

import pytest

from ai.artifacts import ModelArtifactManager
from ai.dataset import DatasetBuilder
from ai.inference import DecisionPolicy, InferenceEngine, InferenceRepository
from ai.model import InterpretableLinearModel
from ai.training import TrainingPipeline
from ai.types import ProposalAction
from config.settings import Settings
from database.models import ModelInferenceLog
from database.session import initialize_database


FEATURES = ("trend", "volatility", "spread")


def rows(count: int = 24):
    start = datetime(2026, 3, 1, tzinfo=timezone.utc); result = []
    labels = ("LONG", "SHORT", "NO_TRADE")
    for index in range(count):
        timestamp = start + timedelta(minutes=index * 5)
        result.append({"timestamp": timestamp, "raw_data_cutoff": timestamp, "target_timestamp": timestamp + timedelta(minutes=5),
                       "feature_snapshot_id": f"snapshot-{index}", "feature_version": "phase3-v1",
                       "features": {"trend": float((index % 3) - 1), "volatility": float(index % 5) / 5, "spread": .1}, "target": labels[index % 3]})
    return result


def test_dataset_is_chronological_and_rejects_future_feature_or_target_leakage():
    builder = DatasetBuilder(FEATURES); observations = builder.build(rows()); dataset = builder.split(observations)
    assert dataset.train[-1].timestamp < dataset.validation[0].timestamp < dataset.out_of_sample[0].timestamp
    bad = rows(5); bad[0]["raw_data_cutoff"] = bad[0]["timestamp"] + timedelta(seconds=1)
    with pytest.raises(ValueError): builder.build(bad)
    bad = rows(5); bad[0]["target_timestamp"] = bad[0]["timestamp"]
    with pytest.raises(ValueError): builder.build(bad)


def test_training_artifact_round_trip_and_fixed_inference_are_deterministic(tmp_path):
    pipeline = TrainingPipeline(DatasetBuilder(FEATURES)); model, metrics, dataset = pipeline.run(rows(), "candidate-ai-v1")
    artifacts = ModelArtifactManager(tmp_path); artifacts.save(model, {"feature_version": "phase3-v1", "split": metrics["split"]})
    restored, metadata = artifacts.load("candidate-ai-v1")
    values = dataset.out_of_sample[0].features
    assert model.probabilities(values) == restored.probabilities(values)
    assert metadata["feature_version"] == "phase3-v1"
    assert metrics["out_of_sample"]["warning"].startswith("Historical")


def test_low_confidence_becomes_no_trade_and_inference_is_logged(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/inference.db"); sessions = initialize_database(settings)
    model = InterpretableLinearModel("flat-v1", FEATURES, [0, 0, 0], [1, 1, 1], [[0, 0, 0, 0] for _ in range(3)])
    engine = InferenceEngine(model, DecisionPolicy(.80), InferenceRepository(sessions))
    snapshot = {"snapshot_id": "feature-1", "feature_version": "phase3-v1", "decision_at": datetime.now(timezone.utc),
                "values": {"trend": 1, "volatility": .4, "spread": .1, "close": 1.1}}
    proposal = engine.infer(snapshot, "RANGING", {"equity": 10_000})
    assert proposal.action is ProposalAction.NO_TRADE
    with sessions() as session:
        saved = session.query(ModelInferenceLog).one()
    assert saved.model_output["action"] == "NO_TRADE"
    assert saved.model_input["trend"] == 1
