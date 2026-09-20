from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from sqlalchemy.orm import Session, sessionmaker
from config.settings import Settings
from database.session import check_database
from mt5.gateway import MT5Gateway

@dataclass(frozen=True, slots=True)
class HealthReport:
    status: str
    timestamp: str
    environment: str
    mode: str
    database: bool
    mt5: bool

class HealthService:
    def __init__(self, settings: Settings, sessions: sessionmaker[Session], mt5: MT5Gateway) -> None:
        self.settings, self.sessions, self.mt5 = settings, sessions, mt5

    def report(self) -> dict:
        database = check_database(self.sessions)
        return asdict(HealthReport("ok" if database else "degraded",
            datetime.now(timezone.utc).isoformat(), self.settings.app_env.value,
            self.settings.trading_mode.value, database, self.mt5.health()))
