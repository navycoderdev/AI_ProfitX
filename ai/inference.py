from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from ai.model import InterpretableLinearModel
from ai.types import ProposalAction, TradeProposal
from database.models import ModelInferenceLog
from monitoring.safety import ProposalSafetyValidator


class DecisionPolicy:
    def __init__(self, minimum_confidence: float = .60) -> None: self.minimum_confidence = minimum_confidence
    def apply(self, probabilities: dict[ProposalAction, float]) -> tuple[ProposalAction, float]:
        action = max(probabilities, key=probabilities.get); confidence = probabilities[action]
        if action is ProposalAction.NO_TRADE or confidence < self.minimum_confidence: return ProposalAction.NO_TRADE, confidence
        return action, confidence


class InferenceRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None: self.sessions = sessions
    def record(self, proposal: TradeProposal, feature_snapshot_id: str, regime: str, portfolio_context: dict, model_input: dict) -> str:
        identifier = str(uuid4())
        with self.sessions() as session:
            session.add(ModelInferenceLog(inference_id=identifier, timestamp=proposal.decision_timestamp, model_version=proposal.model_version,
                feature_version=proposal.feature_version, feature_snapshot_id=feature_snapshot_id, regime=regime,
                portfolio_context=portfolio_context, model_input=model_input, model_output={**asdict(proposal),
                    "action": proposal.action.value, "decision_timestamp": proposal.decision_timestamp.isoformat()})); session.commit()
        return identifier


class InferenceEngine:
    """Pure inference boundary: produces a proposal and log; it has no order or MT5 dependency."""
    def __init__(self, model: InterpretableLinearModel, policy: DecisionPolicy, repository: InferenceRepository, safety: ProposalSafetyValidator | None = None) -> None:
        self.model, self.policy, self.repository, self.safety = model, policy, repository, safety or ProposalSafetyValidator()
    def infer(self, feature_snapshot: dict, regime: str, portfolio_context: dict) -> TradeProposal:
        values = feature_snapshot["values"]; probabilities = self.model.probabilities(values); action, confidence = self.policy.apply(probabilities)
        now = feature_snapshot.get("decision_at") or datetime.now(timezone.utc)
        proposal = TradeProposal(action, confidence, self.model.version, feature_snapshot["feature_version"], now,
            entry_preference=values.get("close"), metadata={"probabilities": {key.value: value for key, value in probabilities.items()}, "regime": regime})
        self.safety.validate(proposal)
        self.repository.record(proposal, feature_snapshot["snapshot_id"], regime, portfolio_context,
                               {name: values[name] for name in self.model.feature_names})
        return proposal
