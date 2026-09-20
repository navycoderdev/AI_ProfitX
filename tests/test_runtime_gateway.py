from datetime import datetime, timezone

import pytest

from core.context import TradingContext
from core.exceptions import ModeViolationError
from core.modes import RuntimeEnvironment, TradingMode
from mt5.runtime_gateway import MT5RuntimeGateway


class FakeTerminal:
    TIMEFRAME_M1 = 1

    def terminal_info(self):
        return {"connected": True}

    def symbol_info(self, _symbol):
        return {"visible": True, "trade_mode": 1}

    def symbol_info_tick(self, _symbol):
        return {"bid": 1.1, "ask": 1.1001, "time": 1_700_000_000}


class FakeManager:
    connected = True
    client = FakeTerminal()

    class settings:
        market_symbols = ("EURUSD",)

    audit = object()


def test_runtime_gateway_reports_terminal_health() -> None:
    assert MT5RuntimeGateway(FakeManager()).health() is True


def test_runtime_gateway_rejects_direct_order_submission() -> None:
    gateway = MT5RuntimeGateway(FakeManager())
    context = TradingContext(None, "EURUSD", "M1", "UNKNOWN", "test", "test", "default",
        RuntimeEnvironment.TEST, TradingMode.PAPER)
    with pytest.raises(ModeViolationError):
        gateway.submit_order(context, {})


def test_runtime_gateway_rejects_unknown_timeframe_before_mt5_read() -> None:
    gateway = MT5RuntimeGateway(FakeManager())
    with pytest.raises(ValueError, match="Unsupported"):
        gateway.candles("EURUSD", "D999", datetime.now(timezone.utc), datetime.now(timezone.utc))


def test_runtime_gateway_returns_actual_tick_observation() -> None:
    observation = MT5RuntimeGateway(FakeManager()).live_market()[0]
    assert observation["symbol"] == "EURUSD"
    assert observation["spread"] == pytest.approx(0.0001)
    assert observation["regime"] is None
