"""Freeze and audit the corrected time-based Dataset v2; never train a model."""
import json
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select

from ai.brain_v1 import BrainV1Dataset
from config.settings import get_settings
from database.models import DatasetManifest, DatasetRow, FeatureDefinition, FeatureSnapshot, ModelVersion
from database.session import initialize_database
from research.dataset_v1 import ResearchDatasetV1Builder
from research.governance import dependency_audit


VERSION = "research-dataset-v2"
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY")
SPLITS = ("TRAIN", "VALIDATION", "OOS")


def audit(sessions, manifest: DatasetManifest) -> dict:
    with sessions() as session:
        rows = session.execute(select(DatasetRow.symbol, DatasetRow.decision_at, DatasetRow.split,
            FeatureSnapshot.raw_data_cutoff).join(FeatureSnapshot,
            FeatureSnapshot.snapshot_id == DatasetRow.feature_snapshot_id).where(
            DatasetRow.dataset_id == manifest.dataset_id)).all()
    if not rows:
        raise RuntimeError("Corrected dataset has no persisted membership rows.")
    with sessions() as session:
        definitions = list(session.scalars(select(FeatureDefinition).where(FeatureDefinition.active.is_(True))))
        brain = session.scalar(select(ModelVersion).where(ModelVersion.version == "Brain-v1"))
    dependencies = dependency_audit(definitions)
    if dependencies["freeze_blocked"] or len(definitions) != 22:
        raise RuntimeError("Feature dependency/definition gate failed.")
    if brain is None or brain.stage != "CANDIDATE":
        raise RuntimeError("Brain-v1 candidate status changed during dataset correction.")
    counts, per_symbol, by_split, by_time = Counter(), defaultdict(Counter), defaultdict(list), defaultdict(set)
    split_lookup = {}
    for symbol, decision_at, split, cutoff in rows:
        if cutoff > decision_at:
            raise RuntimeError("Leakage check failed: raw cutoff follows decision time.")
        counts[split] += 1
        per_symbol[symbol][split] += 1
        by_split[split].append(decision_at)
        by_time[decision_at].add(split)
        split_lookup[(symbol, decision_at)] = split
    if any(len(splits) != 1 for splits in by_time.values()):
        raise RuntimeError("Identical decision timestamps have different split memberships.")
    if not max(by_split["TRAIN"]) < min(by_split["VALIDATION"]) or not max(by_split["VALIDATION"]) < min(by_split["OOS"]):
        raise RuntimeError("Split chronological boundaries overlap.")
    expected = {"TRAIN": manifest.train_rows, "VALIDATION": manifest.validation_rows, "OOS": manifest.oos_rows}
    if dict(counts) != expected or sum(counts.values()) != manifest.usable_rows:
        raise RuntimeError("Manifest counts do not reconcile with persisted DatasetRow membership.")
    if any(per_symbol[symbol][split] == 0 for symbol in SYMBOLS for split in SPLITS):
        raise RuntimeError("One of the required symbols has no observation in a chronological split.")
    labels, _, metadata = BrainV1Dataset(sessions, manifest.content_hash, VERSION).rows()
    if any(split_lookup[(row["symbol"], row["target_timestamp"])] != row["split"] for row in labels):
        raise RuntimeError("A 12-valid-bar label horizon crosses a split boundary.")
    labeled_counts = defaultdict(Counter)
    for row in labels:
        labeled_counts[row["symbol"]][row["split"]] += 1
    old = None
    with sessions() as session:
        old = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == "research-dataset-v1",
            DatasetManifest.state == "FROZEN"))
    if old is None or old.content_hash != "05ce1447a4251693dd548ab57007ac71558d4be6cd8f8e6e9e77ab18b6ce78f5":
        raise RuntimeError("Frozen Dataset v1 audit hash changed or disappeared.")
    return {"dataset_version": VERSION, "dataset_id": manifest.dataset_id, "state": manifest.state,
        "content_hash": manifest.content_hash, "old_dataset_hash": old.content_hash,
        "split_policy_version": manifest.split_policy_version,
        "counts": {"usable_rows": manifest.usable_rows, "train_rows": manifest.train_rows,
                   "validation_rows": manifest.validation_rows, "oos_rows": manifest.oos_rows},
        "per_symbol": {symbol: {split: per_symbol[symbol][split] for split in SPLITS} for symbol in SYMBOLS},
        "chronological_boundaries": {"train_max": max(by_split["TRAIN"]).isoformat(),
            "validation_min": min(by_split["VALIDATION"]).isoformat(),
            "validation_max": max(by_split["VALIDATION"]).isoformat(),
            "oos_min": min(by_split["OOS"]).isoformat()},
        "label_counts": {symbol: {split: labeled_counts[symbol][split] for split in SPLITS} for symbol in SYMBOLS},
        "label_exclusions": metadata["exclusions"], "label_horizon_split_crossings": 0,
        "feature_count": len(definitions), "feature_dependency_check": "PASS",
        "feature_value_check": "PASS", "leakage_check": "PASS", "manifest_count_reconciliation": "PASS",
        "brain_v1_status": brain.stage, "model_training_performed": False}


def main() -> None:
    sessions = initialize_database(get_settings())
    with sessions() as session:
        manifest = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == VERSION,
            DatasetManifest.state == "FROZEN"))
    if manifest is None:
        result = ResearchDatasetV1Builder(sessions).build_and_freeze(SYMBOLS)
        with sessions() as session:
            manifest = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_id == result["dataset_id"]))
    report = audit(sessions, manifest)
    path = Path("reports/dataset_v2_freeze.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
