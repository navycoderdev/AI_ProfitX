"""Broker-derived BTCUSD session and raw candle quality audit."""

import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from config.settings import get_settings
from data.quality import DataQualityEngine
from data.storage import RawMarketDataRepository
from database.session import initialize_database
from research.governance import reason_codes_from_quality


SOURCE = "MT5:ICMarketsSC-Demo"
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4")


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Quality audit requires LIVE disabled.")
    repository = RawMarketDataRepository(initialize_database(settings))
    ingestion = json.loads(Path("reports/ic_btcusd_ingestion.json").read_text(encoding="utf-8"))
    if ingestion["source"] != SOURCE:
        raise RuntimeError("BTCUSD broker lineage mismatch.")
    result = {"broker_server": "ICMarketsSC-Demo", "source": SOURCE, "symbol": "BTCUSD",
              "timeframes": {}, "orders_submitted": 0}
    for tf in TIMEFRAMES:
        bars = repository.bars_as_of("BTCUSD", tf, datetime.max.replace(tzinfo=timezone.utc), source=SOURCE)
        base = asdict(DataQualityEngine().report("BTCUSD", tf, bars))
        gaps = base["timestamp_gaps"]
        signatures = Counter()
        for gap in gaps:
            after, before = datetime.fromisoformat(gap["after"]), datetime.fromisoformat(gap["before"])
            signatures[(after.weekday(), after.strftime("%H:%M"), before.strftime("%H:%M"), gap["duration_seconds"])] += 1
        classified = []
        for gap in gaps:
            after, before = datetime.fromisoformat(gap["after"]), datetime.fromisoformat(gap["before"])
            signature = (after.weekday(), after.strftime("%H:%M"), before.strftime("%H:%M"), gap["duration_seconds"])
            repeated = signatures[signature] >= 3
            classified.append({**gap, "classification": "OBSERVED_REPEATED_BROKER_CLOSURE" if repeated else "POSSIBLE_DATA_GAP",
                               "matching_observations": signatures[signature]})
        invalid = [bar.timestamp.isoformat() for bar in bars if bar.low <= 0 or bar.high < bar.low or
                   bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close)]
        # The governance adapter accepts only known reason codes, so repeated
        # empirical closures are mapped to its existing expected-closure code.
        governance_gaps = [{**gap, "classification": "EXPECTED_MARKET_CLOSURE" if
                            gap["classification"] == "OBSERVED_REPEATED_BROKER_CLOSURE" else "POSSIBLE_DATA_GAP"}
                           for gap in classified]
        codes = reason_codes_from_quality({**base, "gaps": governance_gaps, "invalid_ohlc": invalid})
        unresolved = sum(gap["classification"] == "POSSIBLE_DATA_GAP" for gap in classified)
        status = "FAIL" if base["duplicates"] or base["data_errors"] or invalid else "REVIEW_REQUIRED" if unresolved or base["outliers"] else "PASS"
        result["timeframes"][tf] = {
            "count": len(bars), "start": bars[0].timestamp.isoformat() if bars else None,
            "end": bars[-1].timestamp.isoformat() if bars else None,
            "duplicates": base["duplicates"],
            "out_of_order_rows": sum(item.get("reason") == "out_of_order_timestamp" for item in base["data_errors"]),
            "invalid_ohlc": len(invalid), "missing_values": base["missing_values"],
            "range_outliers": len(base["outliers"]), "gaps": len(classified),
            "range_outlier_detail": base["outliers"],
            "observed_repeated_closures": len(classified) - unresolved,
            "repeated_closure_signatures": [
                {"weekday": weekday, "after_utc": after, "before_utc": before,
                 "duration_seconds": duration, "observations": count}
                for (weekday, after, before, duration), count in sorted(signatures.items()) if count >= 3],
            "unresolved_gaps": [gap for gap in classified if gap["classification"] == "POSSIBLE_DATA_GAP"],
            "reason_codes": codes, "status": status,
        }
    result["quality_gate"] = "PASS" if all(row["status"] == "PASS" for row in result["timeframes"].values()) else "BLOCKED_REVIEW_REQUIRED"
    path = Path("reports/ic_btcusd_quality.json")
    path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"quality_gate": result["quality_gate"], "timeframes": {
        tf: {key: value for key, value in row.items() if key not in
             ("unresolved_gaps", "range_outlier_detail", "repeated_closure_signatures")}
        for tf, row in result["timeframes"].items()}}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
