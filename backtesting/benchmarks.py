from backtesting.engine import BacktestEngine, ReplayStrategy
from backtesting.types import BacktestConfig, BacktestReport
from data.types import MarketBar


class BenchmarkEngine:
    """Compares deterministic baselines; results are research benchmarks, not profitability claims."""
    def __init__(self, engine: BacktestEngine | None = None) -> None: self.engine = engine or BacktestEngine()
    def compare(self, bars: list[MarketBar], strategies: list[ReplayStrategy], config: BacktestConfig) -> dict[str, BacktestReport]:
        return {strategy.version: self.engine.run(bars, strategy, config) for strategy in strategies}
