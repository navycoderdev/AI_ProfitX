import json
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from database.audit import AuditRepository
from database.models import ModelVersion
from models.types import ModelMetadata, ModelStatus


class ModelRegistry:
    """Persistent controlled deployment registry. Production versions and artifacts are immutable."""
    _transitions = {
        ModelStatus.TRAINING: {ModelStatus.CANDIDATE, ModelStatus.REJECTED},
        ModelStatus.CANDIDATE: {ModelStatus.VALIDATING, ModelStatus.REJECTED},
        ModelStatus.VALIDATING: {ModelStatus.PAPER, ModelStatus.REJECTED},
        ModelStatus.PAPER: {ModelStatus.SHADOW, ModelStatus.REJECTED},
        ModelStatus.SHADOW: {ModelStatus.APPROVED, ModelStatus.REJECTED},
        ModelStatus.APPROVED: {ModelStatus.PRODUCTION, ModelStatus.RETIRED, ModelStatus.REJECTED},
        ModelStatus.PRODUCTION: {ModelStatus.APPROVED, ModelStatus.RETIRED},
        ModelStatus.REJECTED: set(), ModelStatus.RETIRED: set(),
    }

    def __init__(self, sessions: sessionmaker[Session], authorized_promoters: frozenset[str] = frozenset(), audit: AuditRepository | None = None) -> None:
        self.sessions, self.authorized_promoters, self.audit = sessions, authorized_promoters, audit

    def next_version(self) -> str:
        with self.sessions() as session: versions = [row.version for row in session.scalars(select(ModelVersion))]
        numbers = [int(value.removeprefix("Brain-v")) for value in versions if value.startswith("Brain-v") and value.removeprefix("Brain-v").isdigit()]
        return f"Brain-v{max(numbers, default=0) + 1}"

    def register_training(self, metadata: ModelMetadata) -> None:
        if metadata.deployment_status is not ModelStatus.TRAINING: raise ValueError("New model versions must begin in TRAINING.")
        with self.sessions() as session:
            if session.scalar(select(ModelVersion).where(ModelVersion.version == metadata.model_id)): raise ValueError("Model version already exists.")
            payload = asdict(metadata); payload["creation_timestamp"] = (metadata.creation_timestamp or datetime.now(timezone.utc)).isoformat(); payload["deployment_status"] = metadata.deployment_status.value
            session.add(ModelVersion(version=metadata.model_id, stage=ModelStatus.TRAINING.value, metadata_json=json.dumps(payload, sort_keys=True))); session.commit()
        self._audit("model.registered", metadata.model_id, {"status": "TRAINING"})

    def transition(self, version: str, target: ModelStatus, actor: str | None = None) -> None:
        if target is ModelStatus.PRODUCTION: self.promote(version, actor or ""); return
        with self.sessions() as session:
            row = self._row(session, version); current = ModelStatus(row.stage)
            if target not in self._transitions[current]: raise ValueError(f"Illegal model transition {current.value} -> {target.value}.")
            row.stage = target.value; row.metadata_json = self._with_status(row.metadata_json, target); session.commit()
        self._audit("model.transition", version, {"status": target.value, "actor": actor})

    def promote(self, version: str, actor: str) -> None:
        self._authorize(actor)
        with self.sessions() as session:
            candidate = self._row(session, version)
            if ModelStatus(candidate.stage) is not ModelStatus.APPROVED: raise ValueError("Only APPROVED candidates may become production.")
            if not json.loads(candidate.metadata_json).get("promotion_gate_passed"):
                raise PermissionError("Production deployment requires a recorded Promotion Gate approval.")
            current = session.scalar(select(ModelVersion).where(ModelVersion.stage == ModelStatus.PRODUCTION.value))
            if current:
                current.stage = ModelStatus.APPROVED.value; current.metadata_json = self._with_status(current.metadata_json, ModelStatus.APPROVED)
            candidate.stage = ModelStatus.PRODUCTION.value; candidate.metadata_json = self._with_status(candidate.metadata_json, ModelStatus.PRODUCTION); session.commit()
        self._audit("model.promoted", version, {"actor": actor})

    def record_promotion_gate(self, version: str, report: dict) -> None:
        """Records the complete passed gate evidence before any production promotion is possible."""
        with self.sessions() as session:
            row = self._row(session, version)
            if ModelStatus(row.stage) is not ModelStatus.SHADOW: raise ValueError("Promotion Gate evidence is accepted only from SHADOW.")
            data = json.loads(row.metadata_json); data["promotion_gate_passed"] = True; data["promotion_gate_report"] = report
            row.metadata_json = json.dumps(data, sort_keys=True); session.commit()
        self._audit("model.promotion_gate_passed", version, {"report": report})

    def rollback(self, version: str, actor: str) -> None:
        self.promote(version, actor); self._audit("model.rollback", version, {"actor": actor})

    def get(self, version: str) -> dict:
        with self.sessions() as session:
            row = self._row(session, version)
            return {"model_id": row.version, "status": row.stage, **json.loads(row.metadata_json)}
    def production(self) -> dict | None:
        with self.sessions() as session:
            row = session.scalar(select(ModelVersion).where(ModelVersion.stage == ModelStatus.PRODUCTION.value))
            return None if not row else {"model_id": row.version, "status": row.stage, **json.loads(row.metadata_json)}
    def lineage(self, version: str) -> list[str]:
        chain = []
        while version:
            item = self.get(version); chain.append(version); version = item.get("parent_model")
        return chain
    def update_training_metadata(self, version: str, **updates: object) -> None:
        with self.sessions() as session:
            row = self._row(session, version)
            if ModelStatus(row.stage) is ModelStatus.PRODUCTION: raise ValueError("Production model metadata/artifact is immutable.")
            data = json.loads(row.metadata_json); data.update(updates); row.metadata_json = json.dumps(data, sort_keys=True); session.commit()
    def _row(self, session: Session, version: str) -> ModelVersion:
        row = session.scalar(select(ModelVersion).where(ModelVersion.version == version))
        if not row: raise KeyError(f"Unknown model version: {version}")
        return row
    def _authorize(self, actor: str) -> None:
        if not actor or actor not in self.authorized_promoters: raise PermissionError("Actor lacks configured model-promotion permission.")
    @staticmethod
    def _with_status(payload: str, status: ModelStatus) -> str:
        data = json.loads(payload); data["deployment_status"] = status.value; return json.dumps(data, sort_keys=True)
    def _audit(self, action: str, version: str, payload: dict) -> None:
        if self.audit: self.audit.write(action, "control-plane", {"model_id": version, **payload})
