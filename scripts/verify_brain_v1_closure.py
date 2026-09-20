"""Read-only Milestone 2 API and local safety evidence in BACKTEST mode."""
import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from config.settings import get_settings
from database.models import DecisionMemory, ModelInferenceLog, ModelVersion, TradeMemory


def main() -> None:
    settings = get_settings()
    if settings.trading_mode.value != "BACKTEST" or settings.allow_live_trading:
        raise RuntimeError("Closure verification requires BACKTEST and LIVE permission false.")
    with TestClient(app) as client:
        brain = client.get("/api/control/ai-brain")
        lab = client.get("/api/control/model-lab")
        runtime = client.get("/api/runtime")
        for response in (brain, lab, runtime):
            response.raise_for_status()
        candidate = next(item for item in brain.json()["candidates"] if item["model_id"] == "Brain-v1")
        model = next(item for item in lab.json()["models"] if item["model_id"] == "Brain-v1")
        simulation = model["oos_simulation"]
        assert candidate["oos_simulation"]["run_id"] == simulation["run_id"]
        assert model["status"] == "CANDIDATE"
        assert model["out_of_sample_metrics"]["per_class"]["SHORT"]["f1"] == 0.0
        assert simulation["trade_scope"] == "OFFLINE_OOS_SIMULATION_TRADES"
        assert simulation["runtime_paper_trades"] == 0
        assert runtime.json()["live_execution_enabled"] is False
        assert type(app.state.mt5_gateway).__name__ == "DisabledMT5Gateway"
        with app.state.sessions() as session:
            counts = {"decision_memories": session.scalar(select(func.count(DecisionMemory.id))),
                      "trade_memories": session.scalar(select(func.count(TradeMemory.id))),
                      "model_inference_logs": session.scalar(select(func.count(ModelInferenceLog.id))),
                      "shadow_decisions": session.scalar(select(func.count(DecisionMemory.id)).where(
                          DecisionMemory.environment == "SHADOW")),
                      "shadow_models": session.scalar(select(func.count(ModelVersion.id)).where(
                          ModelVersion.stage == "SHADOW"))}
        if any(counts.values()):
            raise AssertionError(f"Runtime activity is not zero: {counts}")
        print(json.dumps({"api": "PASS", "model_status": model["status"],
                          "run_id": simulation["run_id"], "oos_predictions": simulation["prediction_count"],
                          "offline_simulated_trades": simulation["simulated_trade_count"],
                          "runtime_counts": counts, "live_permission": settings.allow_live_trading,
                          "mt5_gateway": type(app.state.mt5_gateway).__name__,
                          "shadow_ai": "NOT_STARTED" if not counts["shadow_decisions"] and not counts["shadow_models"] else "ACTIVE"}, sort_keys=True))


if __name__ == "__main__":
    main()
