"""Select and freeze Brain-v4 design using TRAIN and VALIDATION only."""

import json
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from ai.brain_v1 import BrainV1Dataset
from ai.model import ModelTrainer
from ai.types import LabeledObservation, ProposalAction
from config.settings import get_settings
from database.session import initialize_database
from research.brain_v4 import FAMILIES, POLICIES, enriched_features, head_key
from scripts.train_brain_v1 import metric

DATASET_HASH = "8e0d2fa19312a7b85134f72b8f3728a034b8dde1fb737bf867817c071e57932c"
SYMBOLS = tuple(FAMILIES)
ARCHITECTURES = ("shared_base", "shared_context_normalized", "asset_family_heads", "symbol_heads")
THRESHOLDS = (.35, .40, .45, .50, .55, .60)


def observation(row, features):
    return LabeledObservation(row["timestamp"], row["raw_data_cutoff"], row["feature_snapshot_id"],
        "brain-v4-research-features-v1", features, ProposalAction(row["target"]), row["target_timestamp"])


def evaluate(models, rows, threshold, architecture, transform):
    groups = {}
    for symbol in SYMBOLS:
        subset = [row for row in rows if row["symbol"] == symbol]
        obs = tuple(observation(row, transform(row)) for row in subset)
        groups[symbol] = metric(models[head_key(symbol, architecture)], obs, threshold)
        groups[symbol]["actual_class_distribution"] = dict(Counter(row["target"] for row in subset))
    # Macro across symbol metrics prevents the larger crypto partitions from selecting the design alone.
    groups["SYMBOL_MACRO"] = {key: sum(groups[s][key] for s in SYMBOLS) / len(SYMBOLS)
                              for key in ("macro_f1", "balanced_accuracy")}
    return groups


def main():
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Brain-v4 research requires LIVE disabled.")
    sessions = initialize_database(settings)
    rows, base_names, _ = BrainV1Dataset(sessions, DATASET_HASH, "research-dataset-v4").rows()
    # OOS rows are never passed to transforms, fitting, threshold search, metrics, or reports.
    train = [row for row in rows if row["split"] == "TRAIN"]
    validation = [row for row in rows if row["split"] == "VALIDATION"]
    contracts = json.loads(Path("reports/ic_contract_economics.json").read_text(encoding="utf-8"))["symbols"]
    results, fitted = {}, {}
    for architecture in ARCHITECTURES:
        use_enriched = architecture != "shared_base"
        transform = (lambda row: enriched_features(row, contracts)) if use_enriched else (lambda row: row["features"])
        feature_names = tuple(sorted(transform(train[0])))
        keys = sorted({head_key(row["symbol"], architecture) for row in train})
        models = {}
        for key in keys:
            subset = [row for row in train if head_key(row["symbol"], architecture) == key]
            models[key] = ModelTrainer().train(tuple(observation(row, transform(row)) for row in subset),
                feature_names, f"Brain-v4-research-{architecture}-{key}", epochs=15, learning_rate=.08)
        candidates = []
        for threshold in THRESHOLDS:
            scores = evaluate(models, validation, threshold, architecture, transform)
            candidates.append((scores["SYMBOL_MACRO"]["macro_f1"], threshold, scores))
        best_score, threshold, scores = max(candidates, key=lambda item: (item[0], -item[1]))
        results[architecture] = {"feature_count": len(feature_names), "heads": keys,
            "threshold": threshold, "validation": scores, "selection_score": best_score,
            "train_rows": len(train), "validation_rows": len(validation), "oos_accessed": False}
        fitted[architecture] = (models, feature_names)
    selected = max(ARCHITECTURES, key=lambda name: (results[name]["selection_score"], -ARCHITECTURES.index(name)))
    selected_models, selected_names = fitted[selected]
    model_payload = {key: model.as_dict() for key, model in sorted(selected_models.items())}
    frozen = {"version": "brain-v4-pre-oos-config-v1", "state": "FROZEN_PRE_OOS", "dataset_hash": DATASET_HASH,
        "selection_partition": "TRAIN_VALIDATION_ONLY", "oos_accessed": False, "architecture": selected,
        "feature_contract": {"version": "brain-v4-research-features-v1", "names": list(selected_names)},
        "confidence_threshold": results[selected]["threshold"], "threshold_candidates": list(THRESHOLDS),
        "training": {"epochs": 15, "learning_rate": .08, "random_seed": 0},
        "head_mapping": {symbol: head_key(symbol, selected) for symbol in SYMBOLS},
        "stop_target_and_risk_policies": {key: asdict(value) for key, value in POLICIES.items()},
        "global_safety_limits": {"risk_per_trade_fraction": .01, "maximum_drawdown_fraction": .10,
            "maximum_daily_loss_fraction": .03, "maximum_weekly_loss_fraction": .06,
            "maximum_consecutive_losses": 4, "maximum_open_positions": 3},
        "models": model_payload, "status": "RESEARCH_ONLY_NOT_PROMOTED"}
    encoded = json.dumps(frozen, indent=2, sort_keys=True).encode(); frozen["content_hash"] = sha256(encoded).hexdigest()
    Path("reports/brain_v4_pre_oos_config.json").write_text(json.dumps(frozen, indent=2, sort_keys=True), encoding="utf-8")
    report = {"baseline": "Brain-v3 diagnostics d54eb1b", "dataset_hash": DATASET_HASH,
        "partitions_used": {"TRAIN": len(train), "VALIDATION": len(validation)}, "oos_accessed": False,
        "experiments": results, "selected": selected, "frozen_config_hash": frozen["content_hash"],
        "brain_v3_modified": False, "safety": {"orders": 0, "paper": 0, "live": False}}
    Path("reports/brain_v4_research.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"selected": selected, "threshold": results[selected]["threshold"],
        "score": results[selected]["selection_score"], "config_hash": frozen["content_hash"]}, indent=2))


if __name__ == "__main__": main()
