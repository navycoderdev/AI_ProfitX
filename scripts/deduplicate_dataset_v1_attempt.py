"""Remove only duplicate generated Dataset-v1 snapshots from a known concurrent-build incident."""
from sqlalchemy import func, select

from config.settings import get_settings
from database.models import AuditLog, DatasetManifest, DatasetRow, DecisionMemory, FeatureSnapshot, ModelInferenceLog
from database.session import initialize_database


def main() -> None:
    sessions = initialize_database(get_settings())
    with sessions() as session:
        manifest = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == "research-dataset-v1",
            DatasetManifest.state == "FROZEN").order_by(DatasetManifest.frozen_at.desc()))
        if not manifest: raise RuntimeError("Canonical frozen Dataset v1 not found.")
        rows = list(session.scalars(select(DatasetRow).where(DatasetRow.dataset_id == manifest.dataset_id)
            .order_by(DatasetRow.symbol, DatasetRow.decision_at, DatasetRow.id)))
        retained, duplicates, seen = [], [], set()
        for row in rows:
            key = (row.symbol, row.decision_at)
            (duplicates if key in seen else retained).append(row)
            seen.add(key)
        snapshot_ids = [row.feature_snapshot_id for row in duplicates]
        linked_decisions = session.scalar(select(func.count(DecisionMemory.id)).where(DecisionMemory.feature_snapshot_id.in_(snapshot_ids))) or 0
        linked_inferences = session.scalar(select(func.count(ModelInferenceLog.id)).where(ModelInferenceLog.feature_snapshot_id.in_(snapshot_ids))) or 0
        if linked_decisions or linked_inferences:
            raise RuntimeError("Refusing to deduplicate snapshots referenced by decisions or inferences.")
        for row in duplicates: session.delete(row)
        for snapshot in session.scalars(select(FeatureSnapshot).where(FeatureSnapshot.snapshot_id.in_(snapshot_ids))): session.delete(snapshot)
        manifest.usable_rows = len(retained)
        session.add(AuditLog(action="dataset.v1.duplicate_build_deduplicated", environment=get_settings().app_env.value,
            payload={"dataset_id": manifest.dataset_id, "removed_duplicate_memberships": len(duplicates),
                "retained_memberships": len(retained), "raw_market_data_modified": False}))
        session.commit()
        print({"dataset_id": manifest.dataset_id, "removed": len(duplicates), "retained": len(retained)})


if __name__ == "__main__": main()
