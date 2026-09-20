from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class MarketBar:
    source: str
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    tick_volume: float | None = None
    spread: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class MarketTick:
    source: str
    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    last: float | None = None
    volume: float | None = None

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    symbol: str
    timeframe: str
    checked_at: datetime
    observations: int
    duplicates: int
    missing_values: int
    timestamp_gaps: tuple[dict[str, Any], ...]
    outliers: tuple[dict[str, Any], ...]
    data_errors: tuple[dict[str, Any], ...] = ()
