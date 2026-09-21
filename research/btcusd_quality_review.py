"""Deterministic classification helpers for broker-tagged BTCUSD evidence."""

from collections import Counter
from datetime import datetime, timedelta, timezone

from data.candle_builder import TIMEFRAME_SECONDS


TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4")


def gap_signature(timeframe: str, gap: dict) -> tuple:
    after, before = datetime.fromisoformat(gap["after"]), datetime.fromisoformat(gap["before"])
    return timeframe, after.weekday(), after.strftime("%H:%M"), before.strftime("%H:%M"), gap["duration_seconds"]


def unresolved_gaps(timeframe: str, gaps: list[dict]) -> list[dict]:
    signatures = Counter(gap_signature(timeframe, gap) for gap in gaps)
    return [gap for gap in gaps if signatures[gap_signature(timeframe, gap)] < 3]


def missing_interval(timeframe: str, gap: dict) -> tuple[datetime, datetime, int]:
    step = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    after, before = datetime.fromisoformat(gap["after"]), datetime.fromisoformat(gap["before"])
    start = after + step
    missing = max(0, round((before - after).total_seconds() / step.total_seconds()) - 1)
    return start.astimezone(timezone.utc), before.astimezone(timezone.utc), missing


def _equal(left: float, right: float) -> bool:
    return abs(float(left) - float(right)) <= max(1e-8, abs(float(right)) * 1e-10)


def corroborate_outlier(timeframe: str, bar, bars_by_timeframe: dict[str, list]) -> tuple[bool, dict]:
    if timeframe == "M1":
        bucket = bar.timestamp.replace(minute=(bar.timestamp.minute // 5) * 5, second=0, microsecond=0)
        parent = next((row for row in bars_by_timeframe["M5"] if row.timestamp == bucket), None)
        children = [row for row in bars_by_timeframe["M1"]
                    if bucket <= row.timestamp < bucket + timedelta(minutes=5)]
        verified = bool(parent and len(children) == 5 and _equal(children[0].open, parent.open) and
                        _equal(children[-1].close, parent.close) and
                        _equal(max(row.high for row in children), parent.high) and
                        _equal(min(row.low for row in children), parent.low))
        return verified, {"method": "M5_EXACT_AGGREGATION_FROM_FIVE_M1_CANDLES",
                          "parent_timestamp": bucket.isoformat(), "parent_found": parent is not None,
                          "child_count": len(children), "ohlc_exact_match": verified}
    child_tf = "M1"
    seconds = TIMEFRAME_SECONDS[timeframe]
    children = [row for row in bars_by_timeframe[child_tf]
                if bar.timestamp <= row.timestamp < bar.timestamp + timedelta(seconds=seconds)]
    expected = seconds // 60
    verified = bool(len(children) == expected and _equal(children[0].open, bar.open) and
                    _equal(children[-1].close, bar.close) and
                    _equal(max(row.high for row in children), bar.high) and
                    _equal(min(row.low for row in children), bar.low))
    return verified, {"method": f"{timeframe}_EXACT_AGGREGATION_FROM_M1",
                      "child_count": len(children), "expected_child_count": expected,
                      "ohlc_exact_match": verified}


def outlier_review(timeframe: str, bar, bars_by_timeframe: dict[str, list]) -> dict:
    verified, evidence = corroborate_outlier(timeframe, bar, bars_by_timeframe)
    movement = (bar.high - bar.low) / bar.open if bar.open else None
    close_move = (bar.close - bar.open) / bar.open if bar.open else None
    return {"flag_type": "RANGE_OUTLIER", "timeframe": timeframe,
            "start_utc": bar.timestamp.isoformat(),
            "end_utc": (bar.timestamp + timedelta(seconds=TIMEFRAME_SECONDS[timeframe])).isoformat(),
            "missing_candle_count": 0, "range_price": bar.high - bar.low,
            "range_percent": movement, "open_to_close_percent": close_move,
            "classification": "GENUINE_MARKET_MOVE" if verified else "BAD_DATA",
            "broker_availability": "HISTORICAL_CANDLE_PRESENT; CURRENT_TRADE_MODE_FULL",
            "evidence": evidence}
