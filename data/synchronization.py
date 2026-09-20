from datetime import datetime, timezone

from data.candle_builder import TIMEFRAME_SECONDS
from data.storage import RawMarketDataRepository
from data.types import MarketBar


class MultiTimeframeSynchronizer:
    """Returns only closed/known raw observations at one common decision boundary."""
    def __init__(self, repository: RawMarketDataRepository) -> None:
        self.repository = repository

    def as_of(self, symbol: str, timeframes: tuple[str, ...], decision_at: datetime) -> dict[str, list[MarketBar]]:
        if decision_at.tzinfo is None:
            raise ValueError("Multi-timeframe synchronization requires a timezone-aware decision time.")
        cutoff = decision_at.astimezone(timezone.utc)
        result = {}
        for timeframe in timeframes:
            name = timeframe.upper()
            # Raw MT5 bar timestamps are open times. A context observation is
            # knowable only when open_time + duration is at/before the decision.
            result[name] = [bar for bar in self.repository.bars_as_of(symbol, name, cutoff)
                            if bar.timestamp + __import__("datetime").timedelta(seconds=TIMEFRAME_SECONDS[name]) <= cutoff]
        return result

    def latest_closed(self, symbol: str, timeframe: str, decision_at: datetime) -> MarketBar | None:
        bars = self.as_of(symbol, (timeframe,), decision_at)[timeframe.upper()]
        return bars[-1] if bars else None
