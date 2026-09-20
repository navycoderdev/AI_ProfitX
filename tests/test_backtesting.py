from datetime import datetime, timedelta, timezone

from backtesting.benchmarks import BenchmarkEngine
from backtesting.broker import SimulatedBroker
from backtesting.costs import TransactionCostModel
from backtesting.engine import BacktestEngine
from backtesting.types import BacktestConfig, CostAssumptions, OrderIntent, Side
from data.types import MarketBar
from strategies.baselines import BaselineSignal, BreakoutBaseline, MeanReversionBaseline, MovingAverageTrendBaseline


def bars(count: int = 70) -> list[MarketBar]:
    start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    result = []
    for index in range(count):
        close = 1.10 + index * .0002 + (.0008 if index % 9 == 0 else 0)
        result.append(MarketBar("MT5", "EURUSD", "M5", start + timedelta(minutes=index * 5), close - .0001,
                                close + .0003, close - .0003, close, 10, 100, 20))
    return result


class OneLongStrategy:
    version = "test-one-long-v1"
    def __init__(self): self.calls = 0; self.last_history = []
    def evaluate(self, history):
        self.calls += 1; self.last_history = history
        return BaselineSignal(Side.LONG, history[-1].close - .01, history[-1].close + .01, "test") if self.calls == 1 else BaselineSignal(None)


def test_pending_order_fills_next_candle_and_not_same_candle_stop():
    data = bars(4); costs = TransactionCostModel(CostAssumptions(point_size=.00001, commission_per_unit=.01))
    broker = SimulatedBroker(costs, 10_000, 1, 30)
    broker.submit(OrderIntent(Side.LONG, 100, data[0].timestamp, stop_loss=data[1].open + .0001))
    broker.execute_pending(data[1], 1)
    broker.process_exits(data[1], 1)
    assert len(broker.positions) == 1  # no fictional intrabar exit on entry candle
    broker.process_exits(data[2], 2)
    assert len(broker.trades) == 1


def test_costs_partial_fills_and_backtest_metadata_are_recorded():
    assumptions = CostAssumptions(point_size=.00001, commission_per_unit=.01, slippage_points=2, partial_fill_ratio=.5)
    report = BacktestEngine().run(bars(), OneLongStrategy(), BacktestConfig("EURUSD", "M5", position_size=1000, costs=assumptions))
    assert report.trades[0].quantity == 500
    assert report.trades[0].transaction_costs == 10
    assert report.metadata["cost_assumptions"]["partial_fill_ratio"] == .5
    assert report.metadata["strategy_version"] == "test-one-long-v1"
    assert report.metrics["trade_count"] == 1


def test_historical_replay_never_exposes_next_execution_bar_to_strategy():
    strategy = OneLongStrategy(); source = bars()
    BacktestEngine().run(source, strategy, BacktestConfig("EURUSD", "M5"))
    assert strategy.last_history[-1].timestamp < source[-1].timestamp
    assert "next_bar_open" in BacktestEngine().run(source, OneLongStrategy(), BacktestConfig("EURUSD", "M5")).metadata["execution_model"]


def test_baselines_can_be_compared_as_benchmarks():
    source = bars(); config = BacktestConfig("EURUSD", "M5")
    reports = BenchmarkEngine().compare(source, [MovingAverageTrendBaseline(), BreakoutBaseline(), MeanReversionBaseline()], config)
    assert set(reports) == {"ma-trend-baseline-v1", "breakout-baseline-v1", "mean-reversion-baseline-v1"}
    assert all("maximum_drawdown" in report.metrics for report in reports.values())
