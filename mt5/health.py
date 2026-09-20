"""MT5-specific liveness checks used by monitoring and operational tooling."""

from datetime import datetime, timezone

from config.settings import Settings
from database.audit import AuditRepository
from mt5.connection import MT5ConnectionManager
from mt5.services import MT5AccountService


class MT5HealthMonitor:
    """Checks terminal connectivity and optionally refreshes a disconnected session."""

    def __init__(self, manager: MT5ConnectionManager, settings: Settings, audit: AuditRepository) -> None:
        self.manager = manager
        self.settings = settings
        self.audit = audit

    def check(self, reconnect: bool = True) -> dict:
        terminal = self.manager.heartbeat(reconnect=reconnect)
        healthy = bool(terminal.get("connected", False))
        account: dict | None = None
        error: str | None = None
        if healthy:
            try:
                account = MT5AccountService(self.manager, self.settings, self.audit).snapshot()
            except Exception as exc:  # Health endpoints report failures; callers need not crash.
                healthy, error = False, str(exc)
        result = {
            "healthy": healthy,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "terminal": terminal,
            "account_login": account.get("login") if account else None,
            "error": error,
        }
        self.audit.write("mt5.health.check", self.settings.app_env.value, result)
        return result
