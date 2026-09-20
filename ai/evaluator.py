from math import log

from ai.model import InterpretableLinearModel
from ai.types import LabeledObservation
from backtesting.types import BacktestReport


class ModelEvaluator:
    def evaluate(self, model: InterpretableLinearModel, observations: tuple[LabeledObservation, ...]) -> dict:
        if not observations: raise ValueError("Evaluation partition is empty.")
        correct, loss = 0, 0.0
        for item in observations:
            probabilities = model.probabilities(item.features); predicted = max(probabilities, key=probabilities.get)
            correct += int(predicted is item.target); loss -= log(max(probabilities[item.target], 1e-12))
        return {"observations": len(observations), "accuracy": correct / len(observations), "log_loss": loss / len(observations),
                "warning": "Historical classification metrics do not guarantee future profitability."}

    def compare_baselines(self, model_metrics: dict, baseline_reports: dict[str, BacktestReport]) -> dict:
        return {"model": model_metrics, "baselines": {name: {"net_pnl": report.metrics.get("net_pnl"), "maximum_drawdown": report.metrics.get("maximum_drawdown"),
                "trade_count": report.metrics.get("trade_count")} for name, report in baseline_reports.items()},
                "warning": "Comparison is historical research evidence, not a future-performance claim."}
