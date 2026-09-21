from datetime import datetime, timedelta, timezone

from data.types import MarketBar
from research.btcusd_quality_review import missing_interval, outlier_review, unresolved_gaps


UTC = timezone.utc


def candle(tf, at, open_, high, low, close):
    return MarketBar("MT5:ICMarketsSC-Demo", "BTCUSD", tf, at, open_, high, low, close, 1, 1, 1)


def test_m1_range_outlier_requires_exact_m5_cross_timeframe_aggregation():
    at = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    children = [candle("M1", at + timedelta(minutes=i), 100 + i, 102 + i, 99 + i, 101 + i)
                for i in range(5)]
    parent = candle("M5", at, 100, 106, 99, 105)
    review = outlier_review("M1", children[2], {"M1": children, "M5": [parent]})
    assert review["classification"] == "GENUINE_MARKET_MOVE"
    broken = candle("M5", at, 100, 999, 99, 105)
    assert outlier_review("M1", children[2], {"M1": children, "M5": [broken]})["classification"] == "BAD_DATA"


def test_non_recurring_gap_is_quarantinable_half_open_missing_interval():
    gaps = [{"after": "2026-09-20T12:00:00+00:00", "before": "2026-09-20T12:15:00+00:00",
             "duration_seconds": 900.0}]
    assert unresolved_gaps("M5", gaps) == gaps
    start, end, missing = missing_interval("M5", gaps[0])
    assert start.isoformat() == "2026-09-20T12:05:00+00:00"
    assert end.isoformat() == "2026-09-20T12:15:00+00:00"
    assert missing == 2
