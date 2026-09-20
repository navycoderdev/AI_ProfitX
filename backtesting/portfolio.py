from data.types import MarketBar
from backtesting.broker import SimulatedBroker


class PortfolioSimulator:
    """Records marked-to-market portfolio state independently of strategy logic."""
    def __init__(self, broker: SimulatedBroker) -> None: self.broker, self.equity_curve = broker, []

    def mark(self, bar: MarketBar) -> dict:
        equity = self.broker.equity(bar.close)
        previous = self.equity_curve[-1]["equity"] if self.equity_curve else equity
        row = {"timestamp": bar.timestamp, "equity": equity, "cash": self.broker.cash,
               "margin_used": self.broker.margin_used(), "open_positions": len(self.broker.positions),
               "return": (equity - previous) / previous if previous else 0.0}
        self.equity_curve.append(row)
        return row
