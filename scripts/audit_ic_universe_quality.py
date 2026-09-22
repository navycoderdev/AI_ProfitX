"""Gate the broker-tagged Dataset-v4 universe and quarantine unproven gaps."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from config.settings import get_settings
from data.quality import DataQualityEngine
from data.storage import RawMarketDataRepository
from database.session import initialize_database
from research.btcusd_quality_review import TIMEFRAMES, missing_interval, outlier_review, unresolved_gaps
from research.repository import DatasetGovernanceRepository


SOURCE = "MT5:ICMarketsSC-Demo"
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "ETHUSD")
POLICY = "dataset-v4-ic-universe-quality-v1"


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Universe quality audit requires LIVE disabled.")
    sessions = initialize_database(settings)
    raw, governance = RawMarketDataRepository(sessions), DatasetGovernanceRepository(sessions)
    result = {"source": SOURCE, "symbols": {}, "classification_counts": Counter(),
              "quality_gate": "PASS", "orders_submitted": 0}
    for symbol in SYMBOLS:
        by_tf = {tf: raw.bars_as_of(symbol, tf, datetime.max.replace(tzinfo=timezone.utc), source=SOURCE)
                 for tf in TIMEFRAMES}
        if any(not rows for rows in by_tf.values()):
            result["quality_gate"] = "BLOCKED"
            result["symbols"][symbol] = {"status": "BROKER_HISTORY_UNAVAILABLE"}
            continue
        symbol_result = {"timeframes": {}, "status": "PASS"}
        for tf, rows in by_tf.items():
            report = DataQualityEngine().report(symbol, tf, rows)
            unresolved = unresolved_gaps(tf, list(report.timestamp_gaps))
            bad_outliers = []
            indexed = {row.timestamp: row for row in rows}
            for item in report.outliers:
                review = outlier_review(tf, indexed[datetime.fromisoformat(item["timestamp"])], by_tf)
                result["classification_counts"][review["classification"]] += 1
                if review["classification"] == "BAD_DATA":
                    bad_outliers.append(review)
            masks = []
            for gap in unresolved:
                start, end, missing = missing_interval(tf, gap)
                run_id = str(uuid5(NAMESPACE_URL, f"{SOURCE}|{symbol}|{tf}|{start.isoformat()}|{end.isoformat()}"))
                mask = governance.quarantine(policy_version=POLICY, symbol=symbol, timeframe=tf,
                    start_time=start, end_time=end, reason_codes=["POSSIBLE_DATA_GAP"], source_quality_run_id=run_id)
                masks.append({"mask_id": mask.mask_id, "start": start.isoformat(), "end": end.isoformat(),
                              "missing_candles": missing})
                result["classification_counts"]["POSSIBLE_DATA_GAP"] += 1
            for review in bad_outliers:
                start, end = datetime.fromisoformat(review["start_utc"]), datetime.fromisoformat(review["end_utc"])
                run_id = str(uuid5(NAMESPACE_URL, f"{SOURCE}|{symbol}|{tf}|{start.isoformat()}|BAD_DATA"))
                mask = governance.quarantine(policy_version=POLICY, symbol=symbol, timeframe=tf,
                    start_time=start, end_time=end, reason_codes=["BAD_DATA"], source_quality_run_id=run_id)
                masks.append({"mask_id": mask.mask_id, "start": start.isoformat(), "end": end.isoformat(),
                              "missing_candles": 0})
            invalid = sum(item.get("reason") == "invalid_ohlc" for item in report.data_errors)
            ordering = sum(item.get("reason") == "out_of_order_timestamp" for item in report.data_errors)
            status = "BLOCKED" if invalid or ordering or report.duplicates or bad_outliers else "PASS_WITH_QUARANTINE" if masks else "PASS"
            if status == "BLOCKED":
                symbol_result["status"] = result["quality_gate"] = "BLOCKED"
            symbol_result["timeframes"][tf] = {"count": len(rows), "start": rows[0].timestamp.isoformat(),
                "end": rows[-1].timestamp.isoformat(), "gaps": len(report.timestamp_gaps),
                "unresolved_gaps": len(unresolved), "range_outliers": len(report.outliers),
                "bad_data_outliers": len(bad_outliers), "duplicates": report.duplicates,
                "invalid_ohlc": invalid, "out_of_order": ordering, "quarantine_masks": masks, "status": status}
        result["symbols"][symbol] = symbol_result
    result["classification_counts"] = dict(sorted(result["classification_counts"].items()))
    Path("reports/ic_universe_quality.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
