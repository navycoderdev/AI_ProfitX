from data.types import MarketBar
from backtesting.types import CostAssumptions, Side


class TransactionCostModel:
    def __init__(self, assumptions: CostAssumptions) -> None: self.assumptions = assumptions

    def half_spread(self, bar: MarketBar) -> float:
        return ((bar.spread or 0.0) * self.assumptions.point_size) / 2

    def slippage(self) -> float: return self.assumptions.slippage_points * self.assumptions.point_size

    def execution_price(self, mid_price: float, side: Side, bar: MarketBar, entering: bool) -> float:
        direction = 1 if (side is Side.LONG) == entering else -1
        return mid_price + direction * (self.half_spread(bar) + self.slippage())

    def commission(self, quantity: float) -> float: return abs(quantity) * self.assumptions.commission_per_unit
