from execution.types import TradeRecord, TradeState


class OrderStateMachine:
    _allowed = {
        TradeState.SIGNAL_CREATED: {TradeState.RISK_CHECK, TradeState.REJECTED, TradeState.FAILED},
        TradeState.RISK_CHECK: {TradeState.APPROVED, TradeState.REJECTED, TradeState.FAILED},
        TradeState.APPROVED: {TradeState.ORDER_SUBMITTED, TradeState.REJECTED, TradeState.FAILED, TradeState.POSITION_OPEN},
        TradeState.ORDER_SUBMITTED: {TradeState.ORDER_ACKNOWLEDGED, TradeState.REJECTED, TradeState.FAILED, TradeState.POSITION_OPEN},
        TradeState.ORDER_ACKNOWLEDGED: {TradeState.FILLED, TradeState.PARTIALLY_FILLED, TradeState.REJECTED, TradeState.FAILED, TradeState.POSITION_OPEN},
        TradeState.FILLED: {TradeState.POSITION_OPEN, TradeState.FAILED},
        TradeState.PARTIALLY_FILLED: {TradeState.POSITION_OPEN, TradeState.FAILED},
        TradeState.POSITION_OPEN: {TradeState.POSITION_MANAGED, TradeState.EXIT_REQUESTED, TradeState.CLOSED, TradeState.FAILED},
        TradeState.POSITION_MANAGED: {TradeState.POSITION_MANAGED, TradeState.EXIT_REQUESTED, TradeState.CLOSED, TradeState.FAILED},
        TradeState.EXIT_REQUESTED: {TradeState.CLOSED, TradeState.POSITION_OPEN, TradeState.FAILED},
        TradeState.REJECTED: set(), TradeState.CLOSED: set(), TradeState.FAILED: {TradeState.POSITION_OPEN, TradeState.REJECTED},
    }

    def transition(self, trade: TradeRecord, target: TradeState, reason: str) -> None:
        if target not in self._allowed[trade.state]:
            raise ValueError(f"Illegal trade transition {trade.state.value} -> {target.value}.")
        trade.transitions.append({"from": trade.state.value, "to": target.value, "reason": reason})
        trade.state = target
