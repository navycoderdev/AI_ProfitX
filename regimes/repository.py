from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from database.models import RegimeHistory
from regimes.types import RegimeAssessment


class RegimeRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None: self.sessions = sessions
    def record(self, symbol: str, timeframe: str, assessment: RegimeAssessment, decision_id: str | None = None) -> str:
        with self.sessions() as session:
            row = RegimeHistory(timestamp=assessment.timestamp, symbol=symbol.upper(), timeframe=timeframe.upper(), label=assessment.label.value,
                confidence=assessment.confidence, measurements=assessment.measurements, feature_version=assessment.feature_version,
                regime_model_version=assessment.regime_model_version, previous_label=assessment.previous_label.value if assessment.previous_label else None,
                transition=assessment.transition, decision_id=decision_id)
            session.add(row); session.commit(); session.refresh(row)
            return row.regime_id
    def history(self, symbol: str, timeframe: str) -> list[dict]:
        with self.sessions() as session:
            rows = session.scalars(select(RegimeHistory).where(RegimeHistory.symbol == symbol.upper(), RegimeHistory.timeframe == timeframe.upper()).order_by(RegimeHistory.timestamp)).all()
            return [{"regime_id": row.regime_id, "timestamp": row.timestamp, "label": row.label, "confidence": row.confidence,
                     "measurements": row.measurements, "feature_version": row.feature_version, "regime_model_version": row.regime_model_version,
                     "previous_label": row.previous_label, "transition": row.transition, "decision_id": row.decision_id} for row in rows]
