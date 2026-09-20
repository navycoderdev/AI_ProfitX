from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from core.context import TradingContext
from database.audit import AuditRepository
from execution.engine import ExecutionEngine
from execution.exits import ExitManager
from execution.positions import PositionManager
from execution.reconciliation import ExecutionReconciler
from execution.state_machine import OrderStateMachine
from execution.types import ExitPolicy, PositionOwner, TradeRecord, TradeState
from mt5.types import MarketOrder
from risk.engine import DeterministicRiskEngine
from risk.types import AccountRiskState, RiskRequest


class TradeOrchestrator:
    """The only component permitted to turn an approved signal into an MT5 order or position change."""
    def __init__(self, risk: DeterministicRiskEngine, execution: ExecutionEngine, audit: AuditRepository | None = None) -> None:
        self.risk, self.execution, self.audit = risk, execution, audit
        self.machine, self.positions, self.exits, self.reconciler = OrderStateMachine(), PositionManager(), ExitManager(), ExecutionReconciler()
        self._trades: dict[str, TradeRecord] = {}; self._idempotency: dict[str, str] = {}; self._lock = RLock()

    def create_and_execute(self, context: TradingContext, order: MarketOrder, risk_request: RiskRequest,
                           account_state: AccountRiskState, strategy_id: str, model_id: str, magic_number: int,
                           idempotency_key: str) -> TradeRecord:
        with self._lock:
            existing_id = self._idempotency.get(idempotency_key)
            if existing_id: return self._trades[existing_id]
            trade_id = str(uuid4())
            owner = PositionOwner(strategy_id, model_id, trade_id, magic_number)
            owned_order = replace(order, magic=magic_number, comment=trade_id, client_order_id=idempotency_key)
            trade = TradeRecord(order=owned_order, owner=owner, trade_id=trade_id)
            self._trades[trade_id], self._idempotency[idempotency_key] = trade, trade_id
        self._transition(trade, TradeState.RISK_CHECK, "risk_validation_started", context)
        decision = self.risk.evaluate(context, risk_request, account_state)
        trade.add_telemetry("risk_decision", {"status": decision.status.value, "reason_codes": [item.value for item in decision.reason_codes], "size": decision.position_size})
        if not decision.approved:
            self._transition(trade, TradeState.REJECTED, "risk_rejected", context)
            return trade
        self._transition(trade, TradeState.APPROVED, "risk_approved", context)
        try:
            current = self.reconciler.reconcile(trade, self.execution.positions_for(order.symbol))
            if current:
                self._transition(trade, TradeState.POSITION_OPEN, "pre_execution_reconciled", context)
                if trade.position_id is not None: self.positions.register(trade.position_id, trade.owner)
                return trade
            self._transition(trade, TradeState.ORDER_SUBMITTED, "submit_market_order", context)
            result = self.execution.submit(context, trade.order, decision)
            trade.add_telemetry("execution_result", result.audit_payload())
            if not result.accepted:
                self._transition(trade, TradeState.REJECTED if result.final else TradeState.FAILED, result.message, context)
                recovered = self._reconcile_after(trade, context)
                if recovered:
                    self._transition(trade, TradeState.POSITION_OPEN, "reconciliation_recovered_execution", context)
                    if trade.position_id is not None: self.positions.register(trade.position_id, trade.owner)
                return trade
            trade.mt5_ticket, trade.position_id = result.mt5_ticket, result.position_id
            self._transition(trade, TradeState.ORDER_ACKNOWLEDGED, "mt5_acknowledged", context)
            fill_state = TradeState.PARTIALLY_FILLED if result.filled_volume and result.requested_volume and result.filled_volume < result.requested_volume else TradeState.FILLED
            trade.filled_volume = float(result.filled_volume or 0)
            self._transition(trade, fill_state, "mt5_fill_received", context)
            reconciled = self._reconcile_after(trade, context)
            self._transition(trade, TradeState.POSITION_OPEN, "position_opened" if reconciled or result.accepted else "accepted_without_position", context)
            if trade.position_id is not None: self.positions.register(trade.position_id, trade.owner)
        except Exception as exc:
            trade.add_telemetry("execution_exception", {"type": type(exc).__name__, "message": str(exc)})
            if trade.state not in (TradeState.CLOSED, TradeState.REJECTED): self._transition(trade, TradeState.FAILED, "execution_failure", context)
        return trade

    def manage(self, context: TradingContext, trade_id: str, current_price: float, point_size: float,
               policy: ExitPolicy, now: datetime | None = None) -> TradeRecord:
        trade = self._trades[trade_id]; position = self._reconcile_after(trade, context)
        if not position:
            if trade.state in (TradeState.POSITION_OPEN, TradeState.POSITION_MANAGED, TradeState.EXIT_REQUESTED):
                self._transition(trade, TradeState.CLOSED, "position_absent_on_reconciliation", context)
            return trade
        ticket = int(position["ticket"]); module = "exit-manager"
        self.positions.acquire(ticket, module)
        try:
            desired = self.exits.desired_protection(trade, position, current_price, point_size, policy, now)
            if desired is None:
                self._transition(trade, TradeState.POSITION_MANAGED, "no_exit_action", context)
            else:
                stop, target, reason = desired
                if reason == "time_exit": return self.request_exit(context, trade_id, position, 1.0, module)
                result = self.execution.modify(context, ticket, trade.order.symbol, stop, target)
                trade.add_telemetry("protection_modify", result.audit_payload())
                self._transition(trade, TradeState.POSITION_MANAGED if result.accepted else TradeState.FAILED, reason, context)
                self._reconcile_after(trade, context)
        finally:
            self.positions.release(ticket, module)
        return trade

    def request_exit(self, context: TradingContext, trade_id: str, position: dict, fraction: float = 1.0,
                     module_id: str = "manual-exit") -> TradeRecord:
        if not 0 < fraction <= 1: raise ValueError("Exit fraction must be in (0, 1].")
        trade = self._trades[trade_id]; ticket = int(position["ticket"])
        self.positions.acquire(ticket, module_id)
        try:
            self._transition(trade, TradeState.EXIT_REQUESTED, f"exit_fraction={fraction}", context)
            close_position = {**position, "volume": float(position["volume"]) * fraction}
            result = self.execution.close(context, close_position)
            trade.add_telemetry("exit_result", result.audit_payload())
            remaining = self._reconcile_after(trade, context)
            if result.accepted and remaining is None:
                self._transition(trade, TradeState.CLOSED, "exit_confirmed", context); self.positions.close(ticket)
            elif result.accepted:
                self._transition(trade, TradeState.POSITION_OPEN, "partial_exit_confirmed", context)
            else:
                self._transition(trade, TradeState.FAILED, result.message, context)
        finally:
            self.positions.release(ticket, module_id)
        return trade

    def get(self, trade_id: str) -> TradeRecord: return self._trades[trade_id]

    def _reconcile_after(self, trade: TradeRecord, context: TradingContext) -> dict | None:
        position = self.reconciler.reconcile(trade, self.execution.positions_for(trade.order.symbol))
        self._audit("trade.reconciled", context, trade, {"found": bool(position)})
        return position

    def _transition(self, trade: TradeRecord, target: TradeState, reason: str, context: TradingContext) -> None:
        self.machine.transition(trade, target, reason)
        trade.add_telemetry("state_transition", {"state": target.value, "reason": reason})
        self._audit("trade.lifecycle", context, trade, {"state": target.value, "reason": reason})

    def _audit(self, action: str, context: TradingContext, trade: TradeRecord, extra: dict[str, Any]) -> None:
        if self.audit:
            self.audit.write(action, context.environment.value, {"trade_id": trade.trade_id, "state": trade.state.value,
                "owner": {"strategy_id": trade.owner.strategy_id, "model_id": trade.owner.model_id, "magic": trade.owner.magic_number},
                "position_id": trade.position_id, "telemetry_count": len(trade.telemetry), **extra}, str(context.session_id))
