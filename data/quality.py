from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median

from data.candle_builder import TIMEFRAME_SECONDS
from data.types import DataQualityReport, MarketBar


class DataQualityEngine:
    def report(self, symbol: str, timeframe: str, bars: list[MarketBar]) -> DataQualityReport:
        source_keys = [(bar.source, bar.timestamp) for bar in bars]
        source_duplicates = sum(count - 1 for count in Counter(source_keys).values() if count > 1)
        ordering_errors = [{"reason": "out_of_order_timestamp", "at": bars[index].timestamp.isoformat()}
                           for index in range(1, len(bars)) if bars[index].timestamp < bars[index - 1].timestamp]
        invalid_ohlc = [{"reason": "invalid_ohlc", "at": bar.timestamp.isoformat()}
                        for bar in bars if bar.high < bar.low or bar.open > bar.high or bar.open < bar.low or bar.close > bar.high or bar.close < bar.low]
        ordered = sorted(bars, key=lambda bar: bar.timestamp)
        keys = [(bar.source, bar.timestamp) for bar in ordered]
        duplicates = sum(count - 1 for count in Counter(keys).values() if count > 1)
        missing = sum(1 for bar in ordered for value in (bar.open, bar.high, bar.low, bar.close) if value is None)
        expected = timedelta(seconds=TIMEFRAME_SECONDS[timeframe.upper()])
        gaps = tuple({"after": left.timestamp.isoformat(), "before": right.timestamp.isoformat(), "duration_seconds": (right.timestamp - left.timestamp).total_seconds()}
                     for left, right in zip(ordered, ordered[1:]) if right.timestamp - left.timestamp > expected * 1.5)
        ranges = [bar.high - bar.low for bar in ordered if bar.close != 0]
        baseline = median(ranges) if ranges else 0.0
        outliers = tuple({"timestamp": bar.timestamp.isoformat(), "reason": "range_outlier", "range": bar.high - bar.low}
                          for bar in ordered if baseline > 0 and bar.high - bar.low > baseline * 8)
        errors = tuple(([{"reason": "duplicate_timestamp", "count": source_duplicates}] if source_duplicates else []) + ordering_errors + invalid_ohlc)
        return DataQualityReport(symbol.upper(), timeframe.upper(), datetime.now(timezone.utc), len(ordered), duplicates,
                                 missing, gaps, outliers, errors)
