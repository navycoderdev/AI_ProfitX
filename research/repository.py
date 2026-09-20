"""Persistence for Dataset-v1 governance state; it does not build datasets."""
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from database.models import DatasetManifest, DatasetQuarantineMask, DatasetRow
from research.governance import DatasetSpec


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Governance timestamps must be timezone-aware UTC.")
    return value.astimezone(timezone.utc)


def _restore_utc(mask: DatasetQuarantineMask) -> DatasetQuarantineMask:
    # SQLite returns naive DateTime values despite timezone=True; restore the UTC contract at the boundary.
    if mask.start_time.tzinfo is None: mask.start_time = mask.start_time.replace(tzinfo=timezone.utc)
    if mask.end_time.tzinfo is None: mask.end_time = mask.end_time.replace(tzinfo=timezone.utc)
    return mask


class DatasetGovernanceRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None: self.sessions = sessions

    def register_not_built(self, spec: DatasetSpec = DatasetSpec()) -> DatasetManifest:
        with self.sessions() as session:
            frozen = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == spec.version,
                DatasetManifest.state == "FROZEN"))
            if frozen:
                raise ValueError(f"Dataset {spec.version} is already frozen; create a new version instead of overwriting it.")
            existing = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == spec.version,
                DatasetManifest.state == "NOT_BUILT").order_by(DatasetManifest.id.desc()))
            if existing: return existing
            manifest = DatasetManifest(dataset_version=spec.version, state="NOT_BUILT", source=None, symbols=None,
                execution_timeframe=spec.execution_timeframe, context_timeframes=list(spec.context_timeframes),
                feature_set_version=spec.feature_set_version, quality_policy_version=spec.quality_policy_version,
                quarantine_policy_version=spec.quarantine_policy_version, alignment_policy_version=spec.alignment_policy,
                split_policy_version=spec.split_policy_version)
            session.add(manifest); session.commit(); session.refresh(manifest)
            return manifest

    def quarantine(self, *, policy_version: str, symbol: str, timeframe: str, start_time: datetime, end_time: datetime,
                   reason_codes: list[str], source_quality_run_id: str | None) -> DatasetQuarantineMask:
        start, end = _utc(start_time), _utc(end_time)
        if start >= end: raise ValueError("Quarantine interval must be half-open [start, end) with start < end.")
        with self.sessions() as session:
            existing = session.scalar(select(DatasetQuarantineMask).where(DatasetQuarantineMask.policy_version == policy_version,
                DatasetQuarantineMask.symbol == symbol.upper(), DatasetQuarantineMask.timeframe == timeframe.upper(),
                DatasetQuarantineMask.start_time == start, DatasetQuarantineMask.end_time == end))
            if existing: return existing
            mask = DatasetQuarantineMask(policy_version=policy_version, symbol=symbol.upper(), timeframe=timeframe.upper(),
                start_time=start, end_time=end, state="QUARANTINED", reason_codes=sorted(set(reason_codes)),
                source_quality_run_id=source_quality_run_id)
            session.add(mask); session.commit(); session.refresh(mask)
            return _restore_utc(mask)

    def masks(self, symbol: str | None = None, timeframe: str | None = None) -> list[DatasetQuarantineMask]:
        with self.sessions() as session:
            statement = select(DatasetQuarantineMask).order_by(DatasetQuarantineMask.start_time)
            if symbol: statement = statement.where(DatasetQuarantineMask.symbol == symbol.upper())
            if timeframe: statement = statement.where(DatasetQuarantineMask.timeframe == timeframe.upper())
            return [_restore_utc(mask) for mask in session.scalars(statement)]

    def active_at(self, symbol: str, timeframe: str, timestamp: datetime) -> list[DatasetQuarantineMask]:
        at = _utc(timestamp)
        with self.sessions() as session:
            return [_restore_utc(mask) for mask in session.scalars(select(DatasetQuarantineMask).where(DatasetQuarantineMask.symbol == symbol.upper(),
                DatasetQuarantineMask.timeframe == timeframe.upper(), DatasetQuarantineMask.start_time <= at,
                DatasetQuarantineMask.end_time > at))]

    def freeze(self, manifest_id: str, *, source: str, symbols: list[str], source_start: datetime, source_end: datetime,
               counts: dict[str, int], reason_code_summary: dict, content_hash: str) -> DatasetManifest:
        with self.sessions() as session:
            manifest = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_id == manifest_id))
            if not manifest: raise KeyError(f"Unknown dataset manifest: {manifest_id}")
            if manifest.state not in {"NOT_BUILT", "BUILDING"}: raise ValueError("Only a non-built dataset may be frozen.")
            manifest.state, manifest.source, manifest.symbols = "FROZEN", source, sorted(symbols)
            manifest.source_start, manifest.source_end = _utc(source_start), _utc(source_end)
            for field in ("candidate_rows", "usable_rows", "excluded_rows", "quarantined_rows", "train_rows", "validation_rows", "oos_rows"):
                setattr(manifest, field, counts.get(field))
            manifest.reason_code_summary, manifest.content_hash = reason_code_summary, content_hash
            manifest.frozen_at = datetime.now(timezone.utc); session.commit(); session.refresh(manifest)
            return manifest

    def add_rows(self, dataset_id: str, rows: list[dict]) -> None:
        with self.sessions() as session:
            session.add_all([DatasetRow(dataset_id=dataset_id, feature_snapshot_id=row["feature_snapshot_id"], symbol=row["symbol"],
                decision_at=_utc(row["decision_at"]), split=row["split"], exclusion_codes=row.get("exclusion_codes", [])) for row in rows])
            session.commit()
