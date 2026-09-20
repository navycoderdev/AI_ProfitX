from copy import deepcopy
from dataclasses import replace
from math import ceil

from backtesting.engine import BacktestEngine, ReplayStrategy
from backtesting.types import BacktestConfig, BacktestReport, CostAssumptions
from data.types import MarketBar
from validation.types import ValidationResult


class OutOfSampleValidator:
    def validate(self, report: BacktestReport, minimum_trades: int = 20) -> ValidationResult:
        metrics = report.metrics; failures = []
        if int(metrics.get("trade_count", 0) or 0) < minimum_trades: failures.append("INSUFFICIENT_OUT_OF_SAMPLE_TRADES")
        if metrics.get("net_pnl", 0) is None or float(metrics.get("net_pnl", 0)) <= 0: failures.append("NON_POSITIVE_OUT_OF_SAMPLE_NET_PNL")
        return ValidationResult("out_of_sample", not failures, metrics, tuple(failures))


class WalkForwardValidator:
    def __init__(self, engine: BacktestEngine | None = None) -> None: self.engine = engine or BacktestEngine()
    def validate(self, bars: list[MarketBar], strategy: ReplayStrategy, config: BacktestConfig, windows: int = 3) -> ValidationResult:
        if windows < 2 or len(bars) < windows * 4: raise ValueError("Insufficient bars for walk-forward windows.")
        size = len(bars) // windows; reports = []
        for index in range(windows):
            segment = bars[index * size:(index + 1) * size] if index < windows - 1 else bars[index * size:]
            reports.append(self.engine.run(segment, deepcopy(strategy), config))
        pnl = [float(report.metrics["net_pnl"] or 0) for report in reports]
        pass_rate = sum(value > 0 for value in pnl) / len(pnl)
        return ValidationResult("walk_forward", pass_rate >= .60, {"windows": len(reports), "net_pnl_by_window": pnl, "pass_rate": pass_rate,
            "maximum_drawdown": max(float(report.metrics["maximum_drawdown"] or 0) for report in reports)},
            () if pass_rate >= .60 else ("WALK_FORWARD_INSTABILITY",))


class CostStressTester:
    def __init__(self, engine: BacktestEngine | None = None) -> None: self.engine = engine or BacktestEngine()
    def validate(self, bars: list[MarketBar], strategy: ReplayStrategy, config: BacktestConfig, multipliers: tuple[float, ...] = (1.5, 2.0)) -> ValidationResult:
        stressed = {}
        for multiplier in multipliers:
            costs = replace(config.costs, commission_per_unit=config.costs.commission_per_unit * multiplier,
                            slippage_points=config.costs.slippage_points * multiplier)
            report = self.engine.run(bars, deepcopy(strategy), replace(config, costs=costs)); stressed[str(multiplier)] = report.metrics
        failures = tuple(f"COST_STRESS_FAILED_{key}X" for key, value in stressed.items() if float(value.get("net_pnl", 0) or 0) <= 0)
        return ValidationResult("cost_stress", not failures, {"scenarios": stressed}, failures)


class RobustnessAnalyzer:
    def analyze(self, reports: list[BacktestReport]) -> ValidationResult:
        if not reports: raise ValueError("At least one parameter-sensitivity report is required.")
        pnl = [float(report.metrics.get("net_pnl", 0) or 0) for report in reports]
        drawdowns = [float(report.metrics.get("maximum_drawdown", 0) or 0) for report in reports]
        positive_fraction = sum(item > 0 for item in pnl) / len(pnl)
        stable = positive_fraction >= .60
        return ValidationResult("robustness", stable, {"parameter_runs": len(reports), "positive_fraction": positive_fraction,
            "net_pnl_range": [min(pnl), max(pnl)], "maximum_drawdown": max(drawdowns)}, () if stable else ("PARAMETER_SENSITIVITY",))
