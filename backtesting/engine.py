from dataclasses import asdict
from datetime import timezone
from typing import Protocol

from backtesting.broker import SimulatedBroker
from backtesting.costs import TransactionCostModel
from backtesting.performance import PerformanceAnalyzer
from backtesting.portfolio import PortfolioSimulator
from backtesting.replay import HistoricalReplayEngine
from backtesting.types import BacktestConfig, BacktestReport, OrderIntent
from data.types import MarketBar
from strategies.baselines import BaselineSignal


class ReplayStrategy(Protocol):
    version: str
    def evaluate(self, history: list[MarketBar]) -> BaselineSignal: ...


class BacktestEngine:
    def __init__(self, analyzer: PerformanceAnalyzer | None = None) -> None: self.analyzer = analyzer or PerformanceAnalyzer()

    def run(self, bars: list[MarketBar], strategy: ReplayStrategy, config: BacktestConfig) -> BacktestReport:
        if len(bars) < 3: raise ValueError("At least three chronological bars are required for a backtest.")
        replay = HistoricalReplayEngine(bars); costs = TransactionCostModel(config.costs)
        broker = SimulatedBroker(costs, config.starting_cash, config.max_positions, config.costs.leverage)
        portfolio = PortfolioSimulator(broker)
        ordered = replay.bars
        portfolio.mark(ordered[0])
        for index, history, execution_bar in replay.events():
            # A queued decision is executed only on the following candle's open.
            broker.execute_pending(execution_bar, index + 1)
            broker.process_exits(execution_bar, index + 1)
            signal = strategy.evaluate(history)
            if signal.side is not None:
                broker.submit(OrderIntent(signal.side, config.position_size, history[-1].timestamp,
                    signal.stop_loss, signal.take_profit, signal.reason))
            portfolio.mark(execution_bar)
        broker.close_all(ordered[-1]); portfolio.mark(ordered[-1])
        metadata = {"strategy_version": strategy.version, "data_version": config.data_version, "feature_version": config.feature_version,
                    "configuration": asdict(config), "symbol": config.symbol, "timeframe": config.timeframe,
                    "date_range": {"start": ordered[0].timestamp.isoformat(), "end": ordered[-1].timestamp.isoformat()},
                    "cost_assumptions": asdict(config.costs), "execution_model": "next_bar_open_conservative_ohlc"}
        return BacktestReport(metadata, self.analyzer.analyze(config.starting_cash, broker.trades, portfolio.equity_curve),
                              tuple(portfolio.equity_curve), tuple(broker.trades))
