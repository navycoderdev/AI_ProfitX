from datetime import datetime, timedelta, timezone

import pytest

from core.context import TradingContext
from core.modes import RuntimeEnvironment, TradingMode
from execution.orchestrator import TradeOrchestrator
from execution.positions import PositionManager
from execution.state_machine import OrderStateMachine
from execution.types import ExitPolicy, PositionOwner, TradeRecord, TradeState
from mt5.types import ExecutionResult, MarketOrder, OrderSide
from risk.engine import DeterministicRiskEngine
from risk.types import AccountRiskState, RiskProfile, RiskRequest


class FakeExecution:
    def __init__(self, partial: bool = False, uncertain: bool = False):
        self.partial, self.uncertain, self.submits, self.modifies, self.closes = partial, uncertain, 0, 0, 0
        self.positions = []

    def positions_for(self, _symbol): return list(self.positions)
    def submit(self, _context, order, _risk):
        self.submits += 1
        if self.uncertain:
            self.positions.append({"ticket": 501, "symbol": order.symbol, "volume": order.volume, "magic": order.magic, "comment": order.comment, "price_open": 1.1, "sl": order.stop_loss, "tp": order.take_profit})
            return ExecutionResult(False, False, None, "timeout", requested_volume=order.volume)
        volume = order.volume / 2 if self.partial else order.volume
        self.positions.append({"ticket": 501, "symbol": order.symbol, "volume": volume, "magic": order.magic, "comment": order.comment, "price_open": 1.1, "sl": order.stop_loss, "tp": order.take_profit})
        return ExecutionResult(True, True, 10009, "done", mt5_ticket=77, position_id=501, deal_id=88,
                               requested_volume=order.volume, filled_volume=volume)
    def modify(self, _context, ticket, _symbol, stop, target):
        self.modifies += 1
        for position in self.positions:
            if position["ticket"] == ticket: position["sl"], position["tp"] = stop, target
        return ExecutionResult(True, True, 10009, "modified")
    def close(self, _context, position):
        self.closes += 1
        for item in list(self.positions):
            if item["ticket"] == position["ticket"]:
                item["volume"] -= position["volume"]
                if item["volume"] <= 0: self.positions.remove(item)
        return ExecutionResult(True, True, 10009, "closed")


def context():
    return TradingContext("account", "EURUSD", "M5", "RANGE", "model-v1", "strategy-v1", "risk",
                          RuntimeEnvironment.TEST, TradingMode.PAPER)


def risk_request():
    return RiskRequest("EURUSD", "LONG", 1.1, 1.098, 1.104, 1, .00001, 5, 1, "USD", "LONDON")


def state(): return AccountRiskState(10_000, 10_000, 10_000, 9_000)
def order(): return MarketOrder("EURUSD", OrderSide.BUY, 1, stop_loss=1.098, take_profit=1.104)
def orchestrator(fake): return TradeOrchestrator(DeterministicRiskEngine(RiskProfile(allowed_modes=(TradingMode.PAPER,))), fake)


def test_complete_lifecycle_and_idempotent_submission():
    fake = FakeExecution(); system = orchestrator(fake)
    trade = system.create_and_execute(context(), order(), risk_request(), state(), "strategy-v1", "model-v1", 9001, "signal-1")
    repeated = system.create_and_execute(context(), order(), risk_request(), state(), "strategy-v1", "model-v1", 9001, "signal-1")
    assert repeated is trade and fake.submits == 1
    assert trade.state is TradeState.POSITION_OPEN
    assert [item["to"] for item in trade.transitions] == ["RISK_CHECK", "APPROVED", "ORDER_SUBMITTED", "ORDER_ACKNOWLEDGED", "FILLED", "POSITION_OPEN"]
    system.request_exit(context(), trade.trade_id, fake.positions[0])
    assert trade.state is TradeState.CLOSED
    assert fake.closes == 1


def test_partial_fill_and_protection_management_are_recorded():
    fake = FakeExecution(partial=True); system = orchestrator(fake)
    trade = system.create_and_execute(context(), order(), risk_request(), state(), "strategy", "model", 9001, "partial")
    assert any(item["to"] == "PARTIALLY_FILLED" for item in trade.transitions)
    system.manage(context(), trade.trade_id, 1.1015, .00001, ExitPolicy(break_even_after_points=10))
    assert fake.modifies == 1
    assert trade.state is TradeState.POSITION_MANAGED


def test_timeout_reconciles_without_duplicate_retry_and_recovers_position():
    fake = FakeExecution(uncertain=True); system = orchestrator(fake)
    trade = system.create_and_execute(context(), order(), risk_request(), state(), "strategy", "model", 9001, "timeout")
    assert fake.submits == 1
    assert trade.state is TradeState.POSITION_OPEN
    assert any(item["to"] == "FAILED" for item in trade.transitions)
    assert any(item["to"] == "POSITION_OPEN" for item in trade.transitions)


def test_position_manager_prevents_conflicting_modules():
    manager = PositionManager(); owner = PositionOwner("strategy", "model", "trade", 9)
    manager.register(1, owner); manager.acquire(1, "trailing-stop")
    with pytest.raises(ValueError): manager.acquire(1, "manual-exit")
    manager.release(1, "trailing-stop"); manager.acquire(1, "manual-exit")


def test_state_machine_rejects_illegal_transition():
    record = TradeRecord(order(), PositionOwner("strategy", "model", "trade", 7), trade_id="trade")
    with pytest.raises(ValueError): OrderStateMachine().transition(record, TradeState.CLOSED, "illegal")
