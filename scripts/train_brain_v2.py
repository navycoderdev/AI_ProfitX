"""Train Brain-v2 on frozen v3; lock artifact and policy before opening OOS."""
import json
from collections import Counter
from hashlib import sha256
from pathlib import Path

from ai.artifacts import ModelArtifactManager
from ai.brain_v1 import BrainV1Dataset, LABEL_SPEC_V1
from ai.model import ModelTrainer
from ai.types import LabeledObservation, ProposalAction
from config.settings import get_settings
from database.audit import AuditRepository
from database.session import initialize_database
from models.registry import ModelRegistry
from models.types import ModelMetadata, ModelStatus
from scripts.train_brain_v1 import metric


VERSION = "Brain-v2"
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD")


def observations(rows):
    return tuple(LabeledObservation(row["timestamp"], row["raw_data_cutoff"], row["feature_snapshot_id"],
        row["feature_version"], row["features"], ProposalAction(row["target"]), row["target_timestamp"])
        for row in rows)


def scores(model, rows, threshold):
    return {"ALL": metric(model, observations(rows), threshold), **{
        symbol: metric(model, observations([row for row in rows if row["symbol"] == symbol]), threshold)
        for symbol in SYMBOLS}}


def main():
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Offline training requires LIVE disabled.")
    sessions = initialize_database(settings)
    dataset = json.loads(Path("reports/dataset_v3_freeze.json").read_text(encoding="utf-8"))
    if dataset["state"] != "FROZEN" or dataset["dataset_version"] != "research-dataset-v3":
        raise RuntimeError("Dataset v3 is not frozen.")
    rows, names, label_meta = BrainV1Dataset(sessions, dataset["dataset_hash"], "research-dataset-v3").rows()
    groups = {split: [row for row in rows if row["split"] == split] for split in ("TRAIN", "VALIDATION", "OOS")}
    if len(names) != 22 or any(not {row["symbol"] for row in groups[split]} == set(SYMBOLS) for split in groups):
        raise RuntimeError("Brain-v2 feature or universe gate failed.")
    artifact = ModelArtifactManager()
    path = artifact.directory / f"{VERSION}.json"
    if path.exists():
        model, locked = artifact.load(VERSION)
        if locked["dataset_hash"] != dataset["dataset_hash"]:
            raise RuntimeError("Existing immutable Brain-v2 artifact uses another dataset.")
        threshold = locked["confidence_policy"]["threshold"]
        validation = locked["validation"]
    else:
        model = ModelTrainer().train(observations(groups["TRAIN"]), names, VERSION, epochs=25, learning_rate=.08)
        threshold = max((.40, .45, .50, .55, .60),
            key=lambda value: metric(model, observations(groups["VALIDATION"]), value)["macro_f1"])
        validation = scores(model, groups["VALIDATION"], threshold)
        artifact.save(model, {"dataset_version": "research-dataset-v3", "dataset_hash": dataset["dataset_hash"],
            "feature_set_version": "feature-set-v1", "label_spec": LABEL_SPEC_V1,
            "confidence_policy": {"version": "confidence-policy-v2", "threshold": threshold},
            "validation": validation, "seed": 0})
    digest = sha256(path.read_bytes()).hexdigest()
    # OOS is read only after the artifact and confidence threshold are immutable.
    oos = scores(model, groups["OOS"], threshold)
    for split, metrics in (("VALIDATION", validation), ("OOS", oos)):
        for symbol in ("ALL", *SYMBOLS):
            subset = groups[split] if symbol == "ALL" else [row for row in groups[split] if row["symbol"] == symbol]
            metrics[symbol]["actual_class_distribution"] = dict(Counter(row["target"] for row in subset))
    result = {"model_version": VERSION, "status": "CANDIDATE", "artifact_path": str(path),
        "artifact_hash": digest, "dataset_hash": dataset["dataset_hash"],
        "confidence_policy": {"version": "confidence-policy-v2", "threshold": threshold},
        "rows": {split: len(group) for split, group in groups.items()},
        "validation": validation, "oos": oos}
    registry = ModelRegistry(sessions, audit=AuditRepository(sessions))
    metadata = ModelMetadata(VERSION, "Brain-v1", {"dataset_version": "research-dataset-v3",
        "dataset_hash": dataset["dataset_hash"], "feature_names": list(names),
        "train_rows": len(groups["TRAIN"]), "validation_rows": len(groups["VALIDATION"]),
        "oos_rows": len(groups["OOS"])}, {}, "feature-set-v1", "interpretable_multinomial_logistic",
        {"epochs": 25, "learning_rate": .08, "random_seed": 0})
    try:
        current = registry.get(VERSION)
    except KeyError:
        registry.register_training(metadata)
        current = registry.get(VERSION)
    registry.update_training_metadata(VERSION, label_spec=LABEL_SPEC_V1,
        confidence_policy=result["confidence_policy"], validation_metrics=validation,
        out_of_sample_metrics=oos, artifact_path=str(path), artifact_hash=digest,
        label_accounting={"class_distribution": label_meta["class_distribution"],
            "exclusions": label_meta["exclusions"]})
    if current["status"] == "TRAINING":
        registry.transition(VERSION, ModelStatus.CANDIDATE)
    elif current["status"] != "CANDIDATE":
        raise RuntimeError("Brain-v2 status has changed unexpectedly.")
    Path("reports/brain_v2_evaluation.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"artifact_hash": digest, "confidence_policy": result["confidence_policy"],
        "validation_macro_f1": validation["ALL"]["macro_f1"], "oos_macro_f1": oos["ALL"]["macro_f1"]}))


if __name__ == "__main__":
    main()
