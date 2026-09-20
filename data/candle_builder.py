from datetime import datetime, timedelta, timezone

from data.types import MarketBar, MarketTick


TIMEFRAME_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


class CandleBuilder:
    """Builds provisional candles from ticks; historical raw bars remain untouched."""
    def __init__(self, timeframe: str) -> None:
        self.timeframe = timeframe.upper()
        if self.timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"Unsupported candle timeframe: {timeframe}")
        self._current: MarketBar | None = None

    def update(self, tick: MarketTick) -> tuple[MarketBar | None, MarketBar]:
        seconds = TIMEFRAME_SECONDS[self.timeframe]
        stamp = tick.timestamp.astimezone(timezone.utc)
        bucket = stamp - timedelta(seconds=stamp.timestamp() % seconds, microseconds=stamp.microsecond)
        price = tick.last if tick.last is not None else (tick.bid + tick.ask) / 2
        if self._current is None or self._current.timestamp != bucket:
            completed, self._current = self._current, MarketBar("TICK_BUILD", tick.symbol, self.timeframe, bucket,
                price, price, price, price, tick.volume, 1, tick.spread)
            return completed, self._current
        current = self._current
        self._current = MarketBar(current.source, current.symbol, current.timeframe, current.timestamp, current.open,
            max(current.high, price), min(current.low, price), price, (current.volume or 0) + (tick.volume or 0),
            (current.tick_volume or 0) + 1, tick.spread)
        return None, self._current
