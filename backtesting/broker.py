from data.types import MarketBar
from backtesting.costs import TransactionCostModel
from backtesting.types import OrderIntent, Side, SimulatedPosition, SimulatedTrade


class SimulatedBroker:
    """Conservative bar broker: signals fill at the next bar open, never on the signal candle."""
    def __init__(self, costs: TransactionCostModel, starting_cash: float, max_positions: int, leverage: float) -> None:
        self.costs, self.cash, self.max_positions, self.leverage = costs, starting_cash, max_positions, leverage
        self.positions: list[SimulatedPosition] = []; self.pending: list[OrderIntent] = []; self.trades: list[SimulatedTrade] = []
        self._identifier = 0

    def submit(self, order: OrderIntent) -> None: self.pending.append(order)

    def margin_used(self) -> float: return sum(position.initial_margin for position in self.positions)

    def equity(self, mark_price: float) -> float:
        unrealized = sum((mark_price - position.entry_price) * position.quantity *
                         (1 if position.side is Side.LONG else -1) for position in self.positions)
        return self.cash + unrealized

    def execute_pending(self, bar: MarketBar, index: int) -> None:
        pending, self.pending = self.pending, []
        for order in pending:
            if len(self.positions) >= self.max_positions: continue
            quantity = order.quantity * self.costs.assumptions.partial_fill_ratio
            fill = self.costs.execution_price(bar.open, order.side, bar, entering=True)
            margin = abs(fill * quantity) / self.leverage
            if margin > self.cash - self.margin_used(): continue
            self._identifier += 1
            entry_cost = self.costs.commission(quantity)
            self.cash -= entry_cost
            self.positions.append(SimulatedPosition(self._identifier, order.side, quantity, fill, bar.timestamp, index,
                order.stop_loss, order.take_profit, entry_cost, margin))

    def process_exits(self, bar: MarketBar, index: int) -> None:
        for position in list(self.positions):
            if index <= position.opened_index: continue  # no same-candle OHLC assumption for a new fill
            mark = bar.close
            favorable = (bar.high - position.entry_price) if position.side is Side.LONG else (position.entry_price - bar.low)
            adverse = (bar.low - position.entry_price) if position.side is Side.LONG else (position.entry_price - bar.high)
            position.mfe, position.mae = max(position.mfe, favorable), min(position.mae, adverse)
            stop_hit = position.stop_loss is not None and (bar.low <= position.stop_loss if position.side is Side.LONG else bar.high >= position.stop_loss)
            target_hit = position.take_profit is not None and (bar.high >= position.take_profit if position.side is Side.LONG else bar.low <= position.take_profit)
            # When one candle hits both, choose the adverse stop: intrabar sequence is unknown.
            if stop_hit:
                trigger = min(bar.open, position.stop_loss) if position.side is Side.LONG else max(bar.open, position.stop_loss)
                self._close(position, bar, trigger, "stop_loss")
            elif target_hit:
                trigger = max(bar.open, position.take_profit) if position.side is Side.LONG else min(bar.open, position.take_profit)
                self._close(position, bar, trigger, "take_profit")

    def close_all(self, bar: MarketBar) -> None:
        for position in list(self.positions): self._close(position, bar, bar.close, "end_of_data")

    def _close(self, position: SimulatedPosition, bar: MarketBar, mid_price: float, reason: str) -> None:
        fill = self.costs.execution_price(mid_price, position.side, bar, entering=False)
        gross = (fill - position.entry_price) * position.quantity * (1 if position.side is Side.LONG else -1)
        exit_cost = self.costs.commission(position.quantity)
        net = gross - position.entry_cost - exit_cost
        self.cash += gross - exit_cost
        self.positions.remove(position)
        slippage = self.costs.slippage() * position.quantity * 2
        self.trades.append(SimulatedTrade(position.position_id, position.side, position.quantity, position.entry_price, fill,
            position.opened_at, bar.timestamp, gross, net, position.entry_cost + exit_cost, slippage, reason,
            (bar.timestamp - position.opened_at).total_seconds(), position.mfe * position.quantity, position.mae * position.quantity))
