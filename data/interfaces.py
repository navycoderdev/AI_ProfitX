from datetime import datetime
from typing import Protocol


class MarketDataProvider(Protocol):
    def historical(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[dict]: ...
    def latest(self, symbol: str, timeframe: str) -> dict | None: ...
