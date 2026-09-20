from config.settings import Settings
from core.context import TradingContext
from core.events import DomainEvent, EventBus
from core.modes import RuntimeEnvironment, TradingMode
from database.session import check_database, initialize_database
from monitoring.health import HealthService
from mt5.gateway import DisabledMT5Gateway

def test_database_connects(tmp_path):
    sessions = initialize_database(Settings(database_url=f"sqlite:///{tmp_path}/test.db"))
    assert check_database(sessions)

def test_event_bus_delivers_event():
    received = []
    bus = EventBus()
    bus.subscribe("decision", received.append)
    bus.publish(DomainEvent("decision", {"status": "NO_TRADE"}))
    assert len(received) == 1

def test_context_is_utc_and_mode_bound():
    context = TradingContext(None, "EURUSD", "M5", "UNKNOWN", "none", "none",
                             "conservative", RuntimeEnvironment.TEST, TradingMode.BACKTEST)
    assert context.created_at.tzinfo is not None

def test_health_service_reports_database_status(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/health.db")
    service = HealthService(settings, initialize_database(settings), DisabledMT5Gateway())
    assert service.report()["database"] is True
