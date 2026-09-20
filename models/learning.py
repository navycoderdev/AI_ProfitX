from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai.artifacts import ModelArtifactManager
from ai.dataset import DatasetBuilder
from ai.training import TrainingPipeline
from database.models import DecisionMemory, FeatureSnapshot, TradeMemory, TradeMemoryEvent
from models.registry import ModelRegistry
from models.types import ModelMetadata, ModelStatus, RetrainingDecision, RetrainingPolicy


class ExperienceDatasetBuilder:
    """Conservatively labels resolved experience: losses become NO_TRADE, never the opposite-direction counterfactual."""
    def __init__(self, sessions: sessionmaker[Session], feature_names: tuple[str, ...]) -> None:
        self.sessions, self.feature_names = sessions, feature_names

    def build_rows(self) -> list[dict]:
        with self.sessions() as session:
            decisions = list(session.scalars(select(DecisionMemory).where(DecisionMemory.trade_id.is_not(None)).order_by(DecisionMemory.timestamp)))
            features = {row.snapshot_id: row for row in session.scalars(select(FeatureSnapshot))}
            trades = {row.trade_id: row for row in session.scalars(select(TradeMemory))}
            outcome_events = {row.trade_id: row.timestamp for row in session.scalars(select(TradeMemoryEvent).where(TradeMemoryEvent.event_type == "outcome").order_by(TradeMemoryEvent.id))}
        rows = []
        for decision in decisions:
            snapshot, trade = features.get(decision.feature_snapshot_id), trades.get(decision.trade_id)
            pnl = trade.outcome.get("pnl_after_costs") if trade else None
            target_at = outcome_events.get(decision.trade_id)
            if not snapshot or pnl is None or not target_at or target_at <= decision.timestamp: continue
            # We avoid target leakage and avoid asserting a counterfactual SHORT after a losing LONG.
            target = decision.direction if pnl > 0 and decision.direction in ("LONG", "SHORT") else "NO_TRADE"
            rows.append({"timestamp": decision.timestamp, "raw_data_cutoff": snapshot.raw_data_cutoff,
                "target_timestamp": target_at, "feature_snapshot_id": snapshot.snapshot_id, "feature_version": snapshot.feature_version,
                "features": snapshot.values, "target": target})
        return rows


class RetrainingPolicyEngine:
    def evaluate(self, policy: RetrainingPolicy, new_observations: int, last_training_at: datetime | None,
                 performance_drift_investigation: bool = False, data_drift_investigation: bool = False,
                 now: datetime | None = None) -> RetrainingDecision:
        now = now or datetime.now(timezone.utc); reasons = []
        if new_observations >= policy.minimum_new_observations: reasons.append("MINIMUM_NEW_OBSERVATIONS")
        if policy.schedule_interval_seconds and last_training_at and (now - last_training_at).total_seconds() >= policy.schedule_interval_seconds:
            reasons.append("SCHEDULED_RETRAINING")
        if performance_drift_investigation and policy.permit_performance_drift_investigation: reasons.append("PERFORMANCE_DRIFT_INVESTIGATION")
        if data_drift_investigation and policy.permit_data_drift_investigation: reasons.append("DATA_DRIFT_INVESTIGATION")
        return RetrainingDecision(bool(reasons), tuple(reasons))


class CandidateTrainingService:
    """Creates a new Brain version. It never mutates or promotes the existing production model."""
    def __init__(self, registry: ModelRegistry, artifacts: ModelArtifactManager, pipeline: TrainingPipeline) -> None:
        self.registry, self.artifacts, self.pipeline = registry, artifacts, pipeline

    def train_candidate(self, rows: list[dict], feature_version: str, transaction_cost_assumptions: dict,
                        hyperparameters: dict | None = None, regime_metrics: dict | None = None) -> tuple[str, dict]:
        version = self.registry.next_version(); production = self.registry.production(); timestamps = [row["timestamp"] for row in rows]
        metadata = ModelMetadata(version, production["model_id"] if production else None,
            {"observations": len(rows), "feature_names": list(self.pipeline.builder.feature_names)},
            {"start": min(timestamps).isoformat(), "end": max(timestamps).isoformat()}, feature_version,
            "interpretable_multinomial_logistic", hyperparameters or {"epochs": 250, "learning_rate": .08},
            transaction_cost_assumptions=transaction_cost_assumptions, regime_metrics=regime_metrics or {})
        self.registry.register_training(metadata)
        try:
            model, metrics, dataset = self.pipeline.run(rows, version, hyperparameters or {})
            self.artifacts.save(model, {"feature_version": feature_version, "training_dataset": metadata.training_dataset,
                                        "metrics": metrics, "parent_model": metadata.parent_model})
            self.registry.update_training_metadata(version, training_metrics={"observations": len(dataset.train)},
                validation_metrics=metrics["validation"], out_of_sample_metrics=metrics["out_of_sample"],
                training_dataset={**metadata.training_dataset, "split": metrics["split"]})
            self.registry.transition(version, ModelStatus.CANDIDATE)
            return version, metrics
        except Exception:
            self.registry.transition(version, ModelStatus.REJECTED)
            raise
