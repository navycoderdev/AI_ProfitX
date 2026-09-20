from core.context import TradingContext
from mt5.services import MT5OrderService, MT5PositionService
from mt5.types import ExecutionResult, MarketOrder
from risk.types import RiskDecision


class ExecutionEngine:
    """Thin MT5 adapter. Idempotency lives in the orchestrator; MT5 uncertainty reconciles before retry."""
    def __init__(self, orders: MT5OrderService, positions: MT5PositionService) -> None:
        self.orders, self.positions = orders, positions

    def submit(self, context: TradingContext, order: MarketOrder, risk: RiskDecision) -> ExecutionResult:
        return self.orders.submit_market(context, order, risk)

    def close(self, context: TradingContext, position: dict) -> ExecutionResult:
        return self.orders.close_position(context, position)

    def modify(self, context: TradingContext, ticket: int, symbol: str, stop_loss: float | None, take_profit: float | None) -> ExecutionResult:
        return self.orders.modify_position(context, ticket, symbol, stop_loss, take_profit)

    def positions_for(self, symbol: str) -> list[dict]:
        return self.positions.positions(symbol)
