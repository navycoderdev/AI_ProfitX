"""Evaluate the locked Brain-v2 candidate in an offline OOS simulation."""
import json
from pathlib import Path

from ai.artifacts import ModelArtifactManager
from ai.brain_v1 import BrainV1Dataset
from ai.oos_simulation_v2 import simulate_locked_oos_v2
from backtesting.symbol_economics import SymbolEconomics
from config.settings import get_settings
from database.models import BacktestRun, DecisionMemory, TradeMemory
from database.session import initialize_database
from sqlalchemy import func, select


def main():
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Offline simulation requires LIVE disabled.")
    evaluation = json.loads(Path("reports/brain_v2_evaluation.json").read_text(encoding="utf-8"))
    dataset = json.loads(Path("reports/dataset_v3_freeze.json").read_text(encoding="utf-8"))
    economics = json.loads(Path("reports/contract_economics.json").read_text(encoding="utf-8"))
    model, metadata = ModelArtifactManager().load("Brain-v2")
    if metadata["dataset_hash"] != dataset["dataset_hash"] or metadata["confidence_policy"] != evaluation["confidence_policy"]:
        raise RuntimeError("Brain-v2 artifact or confidence policy differs from locked evidence.")
    contracts = {symbol: SymbolEconomics.from_mt5_probe(symbol, probe)
        for symbol, probe in economics["symbols"].items()}
    sessions = initialize_database(settings)
    rows, _, _ = BrainV1Dataset(sessions, dataset["dataset_hash"], "research-dataset-v3").rows()
    result = simulate_locked_oos_v2(model, [row for row in rows if row["split"] == "OOS"],
        threshold=metadata["confidence_policy"]["threshold"], contracts=contracts)
    result.update({"model_version": "Brain-v2", "artifact_hash": evaluation["artifact_hash"],
        "dataset_hash": dataset["dataset_hash"], "offline_only": True,
        "confidence_policy": metadata["confidence_policy"], "mt5_orders": 0})
    with sessions() as session:
        result["runtime_paper_trades"] = session.scalar(select(func.count(TradeMemory.id)).join(
            DecisionMemory, TradeMemory.decision_id == DecisionMemory.decision_id).where(
            DecisionMemory.environment == "PAPER")) or 0
        result["shadow_decisions"] = session.scalar(select(func.count(DecisionMemory.id)).where(
            DecisionMemory.environment == "SHADOW")) or 0
        existing = session.scalar(select(BacktestRun).where(BacktestRun.strategy_version == "Brain-v2",
            BacktestRun.data_version == dataset["dataset_hash"], BacktestRun.symbol == "ALL"))
        if existing is None:
            existing = BacktestRun(strategy_version="Brain-v2", symbol="ALL", timeframe="M5",
                data_version=dataset["dataset_hash"], configuration={"scope": result["trade_scope"],
                "execution_model": result["execution_model"], "cost_assumptions": result["cost_assumptions"]},
                results=result)
            session.add(existing)
            session.commit()
            session.refresh(existing)
        result["run_id"] = existing.run_id
    Path("reports/brain_v2_oos_simulation.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"prediction_count": result["prediction_count"],
        "simulated_trade_count": result["simulated_trade_count"], "net_pnl_usd": result["net_pnl_usd"]}))


if __name__ == "__main__":
    main()
