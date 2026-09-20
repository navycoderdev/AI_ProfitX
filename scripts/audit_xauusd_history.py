"""Review genuine XAUUSD closures and outliers without changing raw observations."""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from config.settings import get_settings
from database.audit import AuditRepository
from database.models import RawMarketBar
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager
from mt5.services import MT5SymbolService


TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4")
MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
METADATA = ("digits", "point", "trade_tick_size", "trade_tick_value", "trade_contract_size",
            "currency_profit", "spread", "spread_float", "trade_mode")


def main() -> None:
    ingestion = json.loads(Path("reports/ingestion_latest.json").read_text(encoding="utf-8"))
    if {item["timeframe"] for item in ingestion} != set(TIMEFRAMES) or any(item["canonical_symbol"] != "XAUUSD" for item in ingestion):
        raise RuntimeError("Expected six genuine XAUUSD timeframe ingestion records.")
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Audit requires LIVE disabled.")
    sessions = initialize_database(settings)
    with sessions() as session:
        bars = {tf: list(session.scalars(select(RawMarketBar).where(RawMarketBar.symbol == "XAUUSD",
            RawMarketBar.timeframe == tf).order_by(RawMarketBar.timestamp))) for tf in TIMEFRAMES}
    minute = {bar.timestamp: bar for bar in bars["M1"]}
    m5 = {bar.timestamp: bar for bar in bars["M5"]}
    m1_gaps = ingestion[0]["quality"]["gaps"]
    observed_daily_dates = {datetime.fromisoformat(gap["after"]).date() for gap in m1_gaps
        if gap["classification"] == "POSSIBLE_DATA_GAP" and
        datetime.fromisoformat(gap["after"]).hour in (21, 22) and datetime.fromisoformat(gap["before"]).hour == 1}
    early_closure_dates = sorted(gap["after"][:10] for gap in m1_gaps
        if gap["classification"] == "POSSIBLE_DATA_GAP" and datetime.fromisoformat(gap["after"]).hour == 21
        and datetime.fromisoformat(gap["before"]).hour == 1)
    if len(observed_daily_dates) < 10:
        raise RuntimeError("Insufficient repeated M1 evidence for XAUUSD daily broker closure.")
    results = {}
    for item in ingestion:
        tf = item["timeframe"]
        quality = item["quality"]
        reviewed_closures = 0
        unresolved_gaps = []
        for gap in quality["gaps"]:
            if gap["classification"] == "EXPECTED_MARKET_CLOSURE":
                continue
            after, before = datetime.fromisoformat(gap["after"]), datetime.fromisoformat(gap["before"])
            if after.date() in observed_daily_dates and after.hour in (21, 22) and before.hour == 1:
                reviewed_closures += 1
            else:
                unresolved_gaps.append(gap)
        mismatch = []
        for outlier in quality["outliers"]:
            # SQLite restores stored UTC timestamps without tzinfo.
            at = datetime.fromisoformat(outlier["timestamp"]).replace(tzinfo=None)
            if tf == "M1":
                bucket = at.replace(minute=(at.minute // 5) * 5, second=0, microsecond=0)
                parent = m5.get(bucket)
                child = minute.get(at)
                valid = bool(parent and child and parent.high >= child.high and parent.low <= child.low)
            else:
                children = [minute.get(at + timedelta(minutes=i)) for i in range(MINUTES[tf])]
                children = [row for row in children if row is not None]
                parent = next((row for row in bars[tf] if row.timestamp == at), None)
                valid = bool(parent and children and abs(parent.high - max(row.high for row in children)) < 1e-8
                             and abs(parent.low - min(row.low for row in children)) < 1e-8)
            if not valid:
                mismatch.append(outlier["timestamp"])
        results[tf] = {"broker_symbol": item["broker_symbol"], "count": len(bars[tf]),
            "start": bars[tf][0].timestamp.isoformat() if bars[tf] else None,
            "end": bars[tf][-1].timestamp.isoformat() if bars[tf] else None,
            "original_quality_status": quality["status"], "original_reason_codes": quality["reason_codes"],
            "duplicates": quality["duplicates"], "out_of_order": sum(x["reason"] == "out_of_order_timestamp" for x in quality["data_errors"]),
            "invalid_ohlc": len(quality["invalid_ohlc"]), "total_gaps": len(quality["gaps"]),
            "reviewed_daily_broker_closures": reviewed_closures, "unresolved_gaps": unresolved_gaps,
            "range_outliers": len(quality["outliers"]), "outlier_cross_timeframe_mismatches": mismatch,
            "quality_gate_passed": not item["errors"] and not unresolved_gaps and not mismatch and
                not quality["duplicates"] and not quality["data_errors"] and not quality["invalid_ohlc"]}
    manager = MT5ConnectionManager(settings, AuditRepository(sessions))
    try:
        manager.connect()
        info = MT5SymbolService(manager, settings, manager.audit).info("XAUUSD")
        contract = {key: info.get(key) for key in METADATA}
    finally:
        if manager.connected:
            manager.disconnect()
    report = {"canonical_symbol": "XAUUSD", "broker_symbol": "XAUUSD",
        "contract_metadata": contract, "timeframes": results,
        "observed_m1_daily_closure_count": len(observed_daily_dates),
        "observed_early_closure_dates": early_closure_dates,
        "quality_gate_passed": all(item["quality_gate_passed"] for item in results.values()),
        "raw_bars_modified": False}
    path = Path("reports/xauusd_history_audit.json")
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"quality_gate_passed": report["quality_gate_passed"],
        "timeframes": {tf: {key: value for key, value in row.items() if key in
            ("count", "original_quality_status", "reviewed_daily_broker_closures", "range_outliers",
             "outlier_cross_timeframe_mismatches", "quality_gate_passed")} for tf, row in results.items()}}, indent=2))


if __name__ == "__main__":
    main()
