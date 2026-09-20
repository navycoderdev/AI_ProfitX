"""Persist a reproducible offline OOS simulation using the existing locked artifact."""
import json
from hashlib import sha256
from pathlib import Path

from sqlalchemy import func, select

from ai.artifacts import ModelArtifactManager
from ai.brain_v1 import BrainV1Dataset, LABEL_SPEC_V1
from ai.oos_simulation import simulate_locked_oos
from config.settings import get_settings
from database.models import BacktestRun, DecisionMemory, TradeMemory
from database.session import initialize_database
from models.registry import ModelRegistry


def main() -> None:
    sessions = initialize_database(get_settings())
    registry = ModelRegistry(sessions)
    registered = registry.get("Brain-v1")
    if registered["status"] != "CANDIDATE":
        raise ValueError("Brain-v1 must remain CANDIDATE.")
    artifact_path = Path("models/artifacts/Brain-v1.json")
    digest = sha256(artifact_path.read_bytes()).hexdigest()
    if digest != registered["artifact_hash"]:
        raise ValueError("Locked artifact hash differs from registry.")
    model, metadata = ModelArtifactManager().load("Brain-v1")
    policy = metadata["confidence_policy"]
    dataset_hash = metadata["dataset_hash"]
    if policy != {"version": "confidence-policy-v1", "threshold": .40} or metadata["label_spec"] != LABEL_SPEC_V1:
        raise ValueError("Locked confidence or label policy differs from expected version.")
    if dataset_hash != registered["training_dataset"]["dataset_hash"]:
        raise ValueError("Artifact and registry dataset hashes differ.")
    rows, names, dataset = BrainV1Dataset(sessions, dataset_hash).rows()
    if tuple(names) != model.feature_names or dataset["manifest"].state != "FROZEN":
        raise ValueError("Frozen dataset features do not match the locked model.")
    oos = [row for row in rows if row["split"] == "OOS"]
    if len(oos) != registered["training_dataset"]["oos_rows"]:
        raise ValueError("OOS label accounting changed.")
    costs = LABEL_SPEC_V1["cost_assumptions"]
    result = simulate_locked_oos(model, oos, threshold=.40, point_size=costs["point_size"],
                                 spread_points=costs["spread_points"],
                                 slippage_points_each_side=costs["slippage_points_each_side"])
    result.update({"model_version": model.version, "artifact_hash": digest,
                   "dataset_version": metadata["dataset_version"], "dataset_hash": dataset_hash,
                   "confidence_policy": policy})
    with sessions() as session:
        result["runtime_paper_trades"] = session.scalar(select(func.count(TradeMemory.id)).join(
            DecisionMemory, TradeMemory.decision_id == DecisionMemory.decision_id).where(
            DecisionMemory.environment == "PAPER")) or 0
        existing = session.scalar(select(BacktestRun).where(BacktestRun.strategy_version == "Brain-v1",
            BacktestRun.data_version == dataset_hash, BacktestRun.symbol == "ALL").order_by(BacktestRun.created_at.desc()))
        if existing and existing.results.get("artifact_hash") == digest:
            record = existing
        else:
            record = BacktestRun(strategy_version="Brain-v1", symbol="ALL", timeframe="M5",
                data_version=dataset_hash, configuration={"scope": result["trade_scope"],
                "execution_model": result["execution_model"], "cost_assumptions": result["cost_assumptions"]},
                results=result)
            session.add(record)
            session.commit()
            session.refresh(record)
        payload = {"run_id": record.run_id, "simulation_timestamp": record.created_at.isoformat(), **record.results}
    report = Path("reports/brain_v1_oos.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
