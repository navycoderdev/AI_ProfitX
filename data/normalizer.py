from datetime import datetime, timezone
from typing import Any

from data.types import MarketBar, MarketTick


class DataNormalizer:
    """Converts provider payloads into typed UTC observations without mutating source data."""

    @staticmethod
    def utc_timestamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        raise ValueError("Market timestamp must be datetime, ISO datetime, or epoch seconds.")

    def bar(self, source: str, symbol: str, timeframe: str, payload: dict[str, Any]) -> MarketBar:
        return MarketBar(source, symbol.upper(), timeframe.upper(), self.utc_timestamp(payload.get("timestamp", payload.get("time"))),
                         float(payload["open"]), float(payload["high"]), float(payload["low"]), float(payload["close"]),
                         self._number(payload.get("real_volume", payload.get("volume"))),
                         self._number(payload.get("tick_volume")), self._number(payload.get("spread")))

    def tick(self, source: str, symbol: str, payload: dict[str, Any]) -> MarketTick:
        return MarketTick(source, symbol.upper(), self.utc_timestamp(payload.get("timestamp", payload.get("time"))),
                          float(payload["bid"]), float(payload["ask"]), self._number(payload.get("last")), self._number(payload.get("volume")))

    @staticmethod
    def _number(value: Any) -> float | None:
        return None if value is None else float(value)
