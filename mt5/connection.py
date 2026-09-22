import importlib
import logging
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from config.settings import Settings
from core.exceptions import GatewayUnavailableError
from core.retry import retry
from database.audit import AuditRepository

log = logging.getLogger(__name__)


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    # MetaTrader5.copy_rates_range returns numpy structured scalars. They do
    # not expose _asdict(), but their dtype field names are the provider schema.
    dtype = getattr(value, "dtype", None)
    names = getattr(dtype, "names", None)
    if names:
        return {key: value[key].item() if hasattr(value[key], "item") else value[key] for key in names}
    if hasattr(value, "_asdict"):
        return dict(value._asdict())
    if isinstance(value, dict):
        return dict(value)
    return {key: getattr(value, key) for key in dir(value) if not key.startswith("_") and not callable(getattr(value, key))}


class MT5ConnectionManager:
    """Thread-safe terminal lifecycle with heartbeat and reconnect capability."""
    def __init__(self, settings: Settings, audit: AuditRepository, client: Any | None = None) -> None:
        self.settings, self.audit = settings, audit
        self._client = client
        self._connected = False
        self._last_heartbeat: datetime | None = None
        self._lock = RLock()

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                self._client = importlib.import_module("MetaTrader5")
            except ModuleNotFoundError as exc:
                raise GatewayUnavailableError("MetaTrader5 package is unavailable.") from exc
        return self._client

    @property
    def connected(self) -> bool:
        return self._connected

    @retry(attempts=2)
    def connect(self) -> bool:
        with self._lock:
            options: dict[str, Any] = {"timeout": self.settings.mt5_timeout_ms}
            if self.settings.mt5_terminal_path:
                options["path"] = self.settings.mt5_terminal_path
            if self.settings.mt5_login and self.settings.mt5_password and self.settings.mt5_server:
                options.update(login=self.settings.mt5_login,
                    password=self.settings.mt5_password.get_secret_value(), server=self.settings.mt5_server)
            ok = bool(self.client.initialize(**options))
            configured_error = self.last_error()
            fallback = False
            if not ok and self.settings.mt5_terminal_path and {"login", "password", "server"} <= options.keys():
                self.client.shutdown()
                ok = bool(self.client.initialize(path=self.settings.mt5_terminal_path,
                                                 timeout=self.settings.mt5_timeout_ms))
                fallback = ok
            self._connected = ok
            error = self.last_error()
            self.audit.write("mt5.connect", self.settings.app_env.value,
                {"success": ok, "error": error, "terminal_session_fallback": fallback,
                 "configured_attempt_error": configured_error if fallback else None})
            if not ok:
                raise GatewayUnavailableError(f"MT5 initialize failed: {error}")
            return True

    def disconnect(self) -> None:
        with self._lock:
            if self._client is not None:
                self.client.shutdown()
            self._connected = False
            self.audit.write("mt5.disconnect", self.settings.app_env.value, {})

    def last_error(self) -> tuple | None:
        try:
            return self.client.last_error()
        except Exception:
            return None

    def terminal_status(self) -> dict[str, Any]:
        info = _mapping(self.client.terminal_info()) if self._connected else {}
        return {"connected": self._connected, "terminal": info, "last_error": self.last_error(),
                "last_heartbeat": self._last_heartbeat.isoformat() if self._last_heartbeat else None}

    def heartbeat(self, reconnect: bool = True) -> dict[str, Any]:
        with self._lock:
            try:
                alive = self._connected and self.client.terminal_info() is not None
                if not alive and reconnect:
                    self._connected = False
                    self.connect()
                elif not alive:
                    self._connected = False
                self._last_heartbeat = datetime.now(timezone.utc)
                self.audit.write("mt5.heartbeat", self.settings.app_env.value, {"healthy": self._connected})
                return self.terminal_status()
            except Exception as exc:
                log.warning("mt5 heartbeat failed: %s", exc)
                self._connected = False
                self.audit.write("mt5.heartbeat", self.settings.app_env.value, {"healthy": False, "error": str(exc)})
                return self.terminal_status()
