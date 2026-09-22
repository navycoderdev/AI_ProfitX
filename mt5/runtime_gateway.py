"""Read-only MT5 gateway used by PAPER and SHADOW operational modes."""

from datetime import datetime, timezone

from core.context import TradingContext
from core.exceptions import ModeViolationError
from mt5.connection import MT5ConnectionManager
from mt5.services import MT5AccountService, MT5MarketDataService, MT5PositionService, MT5SymbolService


class MT5RuntimeGateway:
    """Adapter for monitoring/data reads; it deliberately cannot submit an order."""

    def __init__(self, manager: MT5ConnectionManager) -> None:
        self.manager = manager
        self.settings = manager.settings
        self.audit = manager.audit

    def health(self) -> bool:
        try:
            if self.manager.connected and self.manager.client.terminal_info() is not None:
                return True
            return bool(self.manager.heartbeat(reconnect=True).get("connected"))
        except Exception:
            return False

    def account_snapshot(self) -> dict:
        return MT5AccountService(self.manager, self.settings, self.audit).snapshot()

    def positions(self) -> list[dict]:
        return MT5PositionService(self.manager, self.settings, self.audit).positions()

    def tick(self, symbol: str) -> dict:
        return MT5SymbolService(self.manager, self.settings, self.audit).tick(symbol)

    def live_market(self) -> list[dict]:
        """Return current terminal ticks without fabricating a missing quote."""
        observations: list[dict] = []
        for symbol in self.settings.market_symbols:
            try:
                tick = self.tick(symbol)
                timestamp = tick.get("time_msc", tick.get("time"))
                if timestamp is not None:
                    divisor = 1000 if "time_msc" in tick else 1
                    timestamp = datetime.fromtimestamp(float(timestamp) / divisor, tz=timezone.utc)
                bid, ask = tick.get("bid"), tick.get("ask")
                observations.append({
                    "symbol": symbol,
                    "bid": bid,
                    "ask": ask,
                    "spread": float(ask) - float(bid) if bid is not None and ask is not None else None,
                    "timestamp": timestamp,
                    "timeframe": None,
                    "regime": None,
                    "volatility": None,
                })
            except Exception:
                # One unavailable symbol must not hide healthy symbols from the operator.
                continue
        return observations

    def candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[dict]:
        timeframes = MT5SymbolService(self.manager, self.settings, self.audit).supported_timeframes()
        if timeframe not in timeframes:
            raise ValueError(f"Unsupported MT5 timeframe: {timeframe}")
        return MT5MarketDataService(self.manager, self.settings, self.audit).rates(
            symbol, timeframes[timeframe], start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        )

    def submit_order(self, context: TradingContext, proposal: dict) -> str:
        raise ModeViolationError(
            "Runtime gateway is read-only. Orders must use TradeOrchestrator and MT5OrderService."
        )
