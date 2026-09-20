from collections.abc import Callable
from datetime import datetime
from threading import Event
from time import sleep

from data.normalizer import DataNormalizer
from data.storage import RawMarketDataRepository
from data.types import MarketBar, MarketTick
from mt5.services import MT5MarketDataService


class HistoricalDataCollector:
    def __init__(self, market_data: MT5MarketDataService, repository: RawMarketDataRepository, normalizer: DataNormalizer) -> None:
        self.market_data, self.repository, self.normalizer = market_data, repository, normalizer

    def collect(self, symbol: str, timeframe_name: str, timeframe: int, start: datetime, end: datetime) -> int:
        payloads = self.market_data.rates(symbol, timeframe, start, end)
        bars = [self.normalizer.bar("MT5", symbol, timeframe_name, payload) for payload in payloads]
        return self.repository.append_bars(bars)


class LiveTickCollector:
    """Polling collector; orchestration owns the thread/process and stop event."""
    def __init__(self, market_data: MT5MarketDataService, repository: RawMarketDataRepository, normalizer: DataNormalizer) -> None:
        self.market_data, self.repository, self.normalizer = market_data, repository, normalizer

    def collect_once(self, symbol: str) -> MarketTick:
        tick = self.normalizer.tick("MT5", symbol, self.market_data.tick(symbol))
        self.repository.append_tick(tick)
        return tick

    def run(self, symbols: tuple[str, ...], stop_event: Event, poll_seconds: float = 1.0,
            on_tick: Callable[[MarketTick], None] | None = None) -> None:
        while not stop_event.is_set():
            for symbol in symbols:
                tick = self.collect_once(symbol)
                if on_tick:
                    on_tick(tick)
            stop_event.wait(poll_seconds)
