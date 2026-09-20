from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from config.settings import Settings
from core.context import TradingContext
from core.exceptions import ModeViolationError, RiskRejectedError
from core.modes import RuntimeEnvironment, TradingMode
from database.audit import AuditRepository
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager
from mt5.health import MT5HealthMonitor
from mt5.services import MT5AccountService, MT5MarketDataService, MT5OrderService, MT5PositionService
from mt5.types import MarketOrder, OrderSide
from risk.types import RiskDecision, RiskReasonCode, RiskStatus


class FakeMT5:
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 2
    TRADE_ACTION_REMOVE = 3
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    SYMBOL_TRADE_MODE_DISABLED = 0
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5

    def __init__(self) -> None:
        self.initialized = False
        self.uncertain = False
        self.sent: list[dict] = []

    def initialize(self, **_kwargs):
        self.initialized = True
        return True

    def shutdown(self):
        self.initialized = False

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return SimpleNamespace(connected=self.initialized, trade_allowed=True)

    def account_info(self):
        return SimpleNamespace(login=123, balance=500.0, equity=500.0, margin=0.0,
                               margin_free=500.0, margin_level=0.0, leverage=100, currency="USD")

    def symbol_info(self, _symbol):
        return SimpleNamespace(visible=True, trade_mode=1, volume_min=0.01, volume_max=10.0,
                               volume_step=0.01, point=0.00001, trade_stops_level=10, trade_freeze_level=5)

    def symbol_select(self, _symbol, _visible):
        return True

    def symbol_info_tick(self, _symbol):
        return SimpleNamespace(bid=1.10000, ask=1.10010)

    def copy_rates_range(self, _symbol, _timeframe, _start, _end):
        return [{"time": 1, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "tick_volume": 10}]

    def order_calc_margin(self, *_args):
        return 10.0

    def order_check(self, _request):
        return SimpleNamespace(retcode=0)

    def order_send(self, request):
        self.sent.append(request)
        if self.uncertain:
            return None
        return SimpleNamespace(retcode=10009, order=77, deal=88, volume=request.get("volume", 0),
                               price=request.get("price", 0), comment="done")

    def positions_get(self, symbol=None):
        return ()

    def orders_get(self, symbol=None):
        return ()

    def history_orders_get(self, *_args, **_kwargs):
        return ()

    def history_deals_get(self, *_args, **_kwargs):
        return ()


@pytest.fixture
def mt5_stack(tmp_path):
    settings = Settings(app_env="production", trading_mode="LIVE", allow_live_trading=True,
                        database_url=f"sqlite:///{tmp_path}/mt5.db")
    audit = AuditRepository(initialize_database(settings))
    fake = FakeMT5()
    manager = MT5ConnectionManager(settings, audit, client=fake)
    assert manager.connect()
    return settings, audit, fake, manager


def live_context() -> TradingContext:
    return TradingContext("123", "EURUSD", "M5", "RANGE", "candidate-1", "none",
                          "conservative", RuntimeEnvironment.PRODUCTION, TradingMode.LIVE)


def approved_risk(size: float = .01) -> RiskDecision:
    return RiskDecision(RiskStatus.APPROVED, (RiskReasonCode.APPROVED,), size)


def test_connection_account_market_data_and_health(mt5_stack):
    settings, audit, _fake, manager = mt5_stack
    assert MT5AccountService(manager, settings, audit).snapshot()["equity"] == 500.0
    candles = MT5MarketDataService(manager, settings, audit).rates(
        "EURUSD", 5, datetime.now(timezone.utc) - timedelta(minutes=5), datetime.now(timezone.utc))
    assert candles[0]["close"] == 1.15
    assert MT5HealthMonitor(manager, settings, audit).check()["healthy"] is True
    manager.disconnect()
    assert manager.connected is False
    assert manager.heartbeat(reconnect=True)["connected"] is True


def test_live_order_is_normalized_and_auditable(mt5_stack):
    settings, audit, fake, manager = mt5_stack
    result = MT5OrderService(manager, settings, audit).submit_market(
        live_context(), MarketOrder("EURUSD", OrderSide.BUY, 0.01, stop_loss=1.0990, take_profit=1.1015,
                                    client_order_id="order-1"), approved_risk())
    assert result.accepted is True
    assert result.mt5_ticket == 77
    assert result.deal_id == 88
    assert result.spread_points == 10
    assert len(fake.sent) == 1


def test_uncertain_order_is_reconciled_without_resubmission(mt5_stack):
    settings, audit, fake, manager = mt5_stack
    fake.uncertain = True
    service = MT5OrderService(manager, settings, audit)
    result = service.submit_market(live_context(), MarketOrder("EURUSD", OrderSide.SELL, 0.01,
                                                                client_order_id="uncertain-1"), approved_risk())
    assert result.accepted is False
    assert result.final is True
    assert "no retry" in result.message
    assert len(fake.sent) == 1


def test_live_order_rejected_in_non_live_context(mt5_stack):
    settings, audit, _fake, manager = mt5_stack
    context = TradingContext(None, "EURUSD", "M5", "UNKNOWN", "none", "none", "conservative",
                             RuntimeEnvironment.TEST, TradingMode.PAPER)
    with pytest.raises(ModeViolationError):
        MT5OrderService(manager, settings, audit).submit_market(context, MarketOrder("EURUSD", OrderSide.BUY, 0.01), approved_risk())


def test_risk_rejection_cannot_reach_mt5(mt5_stack):
    settings, audit, fake, manager = mt5_stack
    rejected = RiskDecision(RiskStatus.REJECTED, (RiskReasonCode.MAX_DRAWDOWN,), None)
    with pytest.raises(RiskRejectedError):
        MT5OrderService(manager, settings, audit).submit_market(live_context(), MarketOrder("EURUSD", OrderSide.BUY, 0.01), rejected)
    assert fake.sent == []


def test_position_and_history_services_handle_empty_mt5_responses(mt5_stack):
    settings, audit, _fake, manager = mt5_stack
    service = MT5PositionService(manager, settings, audit)
    assert service.positions() == []
    assert service.pending_orders() == []
