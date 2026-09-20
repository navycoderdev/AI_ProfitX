from contextlib import asynccontextmanager
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from api.router import router
from config.settings import get_settings
from core.logging import configure_logging
from database.session import initialize_database
from database.audit import AuditRepository
from models.registry import ModelRegistry
from monitoring.health import HealthService
from monitoring.alerts import AlertService
from monitoring.control import EmergencyStopService
from monitoring.control_center import ControlCenter
from monitoring.recovery import StartupRecoveryService
from mt5.gateway import DisabledMT5Gateway
from mt5.connection import MT5ConnectionManager
from mt5.runtime_gateway import MT5RuntimeGateway
from core.modes import TradingMode

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app.state.settings = settings
    app.state.sessions = initialize_database(settings)
    app.state.audit = AuditRepository(app.state.sessions)
    app.state.mt5_manager = MT5ConnectionManager(settings, app.state.audit)
    app.state.mt5_gateway = DisabledMT5Gateway()
    if settings.trading_mode is not TradingMode.BACKTEST:
        app.state.mt5_gateway = MT5RuntimeGateway(app.state.mt5_manager)
        try:
            app.state.mt5_manager.connect()
        except Exception as exc:
            logging.getLogger(__name__).warning("MT5 startup connection unavailable: %s", exc)
    app.state.health_service = HealthService(settings, app.state.sessions, app.state.mt5_gateway)
    app.state.alerts = AlertService(app.state.sessions)
    app.state.registry = ModelRegistry(app.state.sessions, audit=app.state.audit)
    app.state.emergency_stop = EmergencyStopService(app.state.sessions, settings, app.state.audit)
    app.state.control_center = ControlCenter(settings, app.state.sessions, app.state.registry, app.state.alerts,
        app.state.emergency_stop, app.state.health_service.report,
        positions=lambda: app.state.mt5_gateway.positions() if app.state.mt5_gateway.health() else [],
        account=lambda: app.state.mt5_gateway.account_snapshot() if app.state.mt5_gateway.health() else {},
        live_market=lambda: app.state.mt5_gateway.live_market() if app.state.mt5_gateway.health() else [])
    app.state.startup_recovery = StartupRecoveryService(app.state.sessions, app.state.registry, app.state.emergency_stop,
        app.state.alerts, app.state.audit)
    app.state.startup_recovery.recover()
    logging.getLogger(__name__).info("platform foundation started")
    yield
    if app.state.mt5_manager.connected:
        app.state.mt5_manager.disconnect()
    logging.getLogger(__name__).info("platform foundation stopped")

app = FastAPI(title="MT5 Adaptive AI Trading Platform", version="0.1.0", lifespan=lifespan)
app.include_router(router, prefix="/api")

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")


@app.get("/", include_in_schema=False)
def control_center_frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")
