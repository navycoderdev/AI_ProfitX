from datetime import datetime, timedelta, timezone

import pytest

from ai.artifacts import ModelArtifactManager
from ai.dataset import DatasetBuilder
from ai.training import TrainingPipeline
from config.settings import Settings
from database.session import initialize_database
from features.snapshots import FeatureSnapshotService
from memory.builder import ExperienceBuilder
from memory.repositories import DecisionMemoryRepository, MarketSnapshotRepository, TradeMemoryRepository
from memory.types import AIDecisionMemory, PostTradeOutcome, PreTradeSnapshot
from core.context import TradingContext
from core.modes import RuntimeEnvironment, TradingMode
from models.learning import CandidateTrainingService, ExperienceDatasetBuilder, RetrainingPolicyEngine
from models.registry import ModelRegistry
from models.types import ModelMetadata, ModelStatus, RetrainingPolicy


def metadata(version, parent=None):
    return ModelMetadata(version, parent, {"observations": 10}, {"start": "2026-01-01", "end": "2026-02-01"}, "phase3-v1",
                         "interpretable_multinomial_logistic", {"epochs": 10})


def approve(registry, version):
    for status in (ModelStatus.CANDIDATE, ModelStatus.VALIDATING, ModelStatus.PAPER, ModelStatus.SHADOW): registry.transition(version, status)
    registry.record_promotion_gate(version, {"passed": True})
    registry.transition(version, ModelStatus.APPROVED)


def rows(count=18):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc); labels = ("LONG", "SHORT", "NO_TRADE")
    return [{"timestamp": start + timedelta(minutes=index), "raw_data_cutoff": start + timedelta(minutes=index),
             "target_timestamp": start + timedelta(minutes=index + 1), "feature_snapshot_id": f"f-{index}", "feature_version": "phase3-v1",
             "features": {"trend": float(index % 3), "volatility": float(index % 4), "spread": .1}, "target": labels[index % 3]} for index in range(count)]


def test_registry_lineage_production_immutability_and_authorized_rollback(tmp_path):
    sessions = initialize_database(Settings(database_url=f"sqlite:///{tmp_path}/registry.db")); registry = ModelRegistry(sessions, frozenset({"reviewer"}))
    registry.register_training(metadata("Brain-v1")); approve(registry, "Brain-v1")
    with pytest.raises(PermissionError): registry.promote("Brain-v1", "ai-model")
    registry.promote("Brain-v1", "reviewer")
    with pytest.raises(ValueError): registry.update_training_metadata("Brain-v1", validation_metrics={"changed": True})
    registry.register_training(metadata("Brain-v2", "Brain-v1")); approve(registry, "Brain-v2"); registry.promote("Brain-v2", "reviewer")
    assert registry.production()["model_id"] == "Brain-v2"
    registry.rollback("Brain-v1", "reviewer")
    assert registry.production()["model_id"] == "Brain-v1"
    assert registry.lineage("Brain-v2") == ["Brain-v2", "Brain-v1"]


def test_retraining_policy_does_not_train_after_one_loss_but_allows_governed_triggers():
    engine = RetrainingPolicyEngine(); policy = RetrainingPolicy(minimum_new_observations=10)
    assert engine.evaluate(policy, 0, None).should_train is False
    assert engine.evaluate(policy, 10, None).reasons == ("MINIMUM_NEW_OBSERVATIONS",)
    assert "PERFORMANCE_DRIFT_INVESTIGATION" in engine.evaluate(policy, 0, None, performance_drift_investigation=True).reasons


def test_experiences_create_time_safe_training_rows(tmp_path):
    sessions = initialize_database(Settings(database_url=f"sqlite:///{tmp_path}/experience.db")); features = FeatureSnapshotService(sessions)
    decision_time = datetime.now(timezone.utc) - timedelta(hours=1)
    feature_id = features.save("EURUSD", "M5", decision_time, "phase3-v1", decision_time, {"trend": 1, "volatility": .4, "spread": .1})
    builder = ExperienceBuilder(MarketSnapshotRepository(sessions), DecisionMemoryRepository(sessions), TradeMemoryRepository(sessions), features)
    context = TradingContext(None, "EURUSD", "M5", "RANGE", "model", "strategy", "risk", RuntimeEnvironment.TEST, TradingMode.PAPER)
    decision_id = builder.record_decision(context, PreTradeSnapshot(decision_time, "EURUSD", "M5", {}), AIDecisionMemory("LONG", .8, 1.1, 1.09, 1.12), feature_id, trade_id="trade-1")
    builder.record_outcome("trade-1", PostTradeOutcome(1.12, "target", 60, 2, 1.5, .01, .02, -.01))
    generated = ExperienceDatasetBuilder(sessions, ("trend", "volatility", "spread")).build_rows()
    assert generated[0]["target"] == "LONG" and generated[0]["target_timestamp"] > generated[0]["timestamp"]


def test_candidate_training_creates_new_version_and_immutable_artifact(tmp_path):
    sessions = initialize_database(Settings(database_url=f"sqlite:///{tmp_path}/candidate.db")); registry = ModelRegistry(sessions)
    service = CandidateTrainingService(registry, ModelArtifactManager(tmp_path / "artifacts"), TrainingPipeline(DatasetBuilder(("trend", "volatility", "spread"))))
    version, metrics = service.train_candidate(rows(), "phase3-v1", {"spread": "historical"})
    assert version == "Brain-v1" and registry.get(version)["status"] == "CANDIDATE"
    assert metrics["split"]["train"] > 0
    with pytest.raises(FileExistsError): service.artifacts.save(service.artifacts.load(version)[0], {})
