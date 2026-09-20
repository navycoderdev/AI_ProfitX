from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from database.models import FeatureSnapshot


class FeatureSnapshotService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def save(self, symbol: str, timeframe: str, decision_at: datetime, feature_version: str,
             raw_data_cutoff: datetime, values: dict, context: dict | None = None) -> str:
        if raw_data_cutoff > decision_at:
            raise ValueError("Feature snapshot cutoff cannot be after decision time.")
        snapshot_id = str(uuid4())
        with self.sessions() as session:
            session.add(FeatureSnapshot(snapshot_id=snapshot_id, symbol=symbol.upper(), timeframe=timeframe.upper(),
                decision_at=decision_at.astimezone(timezone.utc), feature_version=feature_version,
                raw_data_cutoff=raw_data_cutoff.astimezone(timezone.utc), values=values, context=context or {}))
            session.commit()
        return snapshot_id

    def get(self, snapshot_id: str) -> dict:
        with self.sessions() as session:
            row = session.scalar(select(FeatureSnapshot).where(FeatureSnapshot.snapshot_id == snapshot_id))
            if not row: raise KeyError(f"Unknown feature snapshot: {snapshot_id}")
            return {"snapshot_id": row.snapshot_id, "symbol": row.symbol, "timeframe": row.timeframe,
                    "decision_at": row.decision_at, "feature_version": row.feature_version,
                    "raw_data_cutoff": row.raw_data_cutoff, "values": row.values, "context": row.context}

    def save_batch(self, snapshots: list[dict]) -> list[str]:
        """Persist real snapshots in one transaction; all cutoffs are validated before writing."""
        rows = []
        for item in snapshots:
            decision, cutoff = item["decision_at"].astimezone(timezone.utc), item["raw_data_cutoff"].astimezone(timezone.utc)
            if cutoff > decision: raise ValueError("Feature snapshot cutoff cannot be after decision time.")
            rows.append(FeatureSnapshot(snapshot_id=str(uuid4()), symbol=item["symbol"].upper(), timeframe=item["timeframe"].upper(),
                decision_at=decision, feature_version=item["feature_version"], raw_data_cutoff=cutoff,
                values=item["values"], context=item.get("context", {})))
        snapshot_ids = [row.snapshot_id for row in rows]
        with self.sessions() as session:
            session.add_all(rows); session.commit()
        return snapshot_ids
