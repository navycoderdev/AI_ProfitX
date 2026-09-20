from datetime import datetime
from typing import Protocol

from core.context import TradingContext
from core.exceptions import GatewayUnavailableError


class MT5Gateway(Protocol):
    def health(self) -> bool: ...
    def account_snapshot(self) -> dict: ...
    def candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[dict]: ...
    def submit_order(self, context: TradingContext, proposal: dict) -> str: ...


class DisabledMT5Gateway:
    """Safe gateway used by the foundation until Phase 3 implements MT5 I/O."""
    def health(self) -> bool:
        return False

    def _unavailable(self):
        raise GatewayUnavailableError("MT5 gateway is not implemented in Phase 1.")

    def account_snapshot(self) -> dict:
        self._unavailable()

    def candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[dict]:
        self._unavailable()

    def submit_order(self, context: TradingContext, proposal: dict) -> str:
        self._unavailable()
