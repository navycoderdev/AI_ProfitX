from collections.abc import Iterator
from data.types import MarketBar


class HistoricalReplayEngine:
    """Yields a decision-safe history and the next unobserved execution candle."""
    def __init__(self, bars: list[MarketBar]) -> None:
        self.bars = sorted(bars, key=lambda bar: bar.timestamp)
        if len({bar.timestamp for bar in self.bars}) != len(self.bars): raise ValueError("Replay bars contain duplicate timestamps.")

    def events(self, warmup: int = 1) -> Iterator[tuple[int, list[MarketBar], MarketBar]]:
        for index in range(warmup, len(self.bars) - 1):
            yield index, self.bars[:index + 1], self.bars[index + 1]
