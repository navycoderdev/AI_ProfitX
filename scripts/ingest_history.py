"""Download immutable MT5 candles into the local raw-market database."""
import argparse
import json
from datetime import datetime, timezone

from config.settings import get_settings
from data.operations import ChunkedHistoricalIngestor, quality_report
from data.storage import RawMarketDataRepository
from database.audit import AuditRepository
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager
from mt5.services import MT5MarketDataService


def parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--timeframes", nargs="+", required=True)
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--chunk-days", type=int, default=30)
    args = parser.parse_args()
    settings, sessions = get_settings(), initialize_database(get_settings())
    audit, manager = AuditRepository(sessions), MT5ConnectionManager(settings, AuditRepository(sessions))
    manager.connect()
    try:
        repository = RawMarketDataRepository(sessions)
        ingestor = ChunkedHistoricalIngestor(MT5MarketDataService(manager, settings, audit), repository)
        results = []
        for symbol in args.symbols:
            for timeframe in args.timeframes:
                result = ingestor.ingest(symbol, timeframe, parse_date(args.start), parse_date(args.end), args.chunk_days)
                result["quality"] = quality_report(repository, symbol, timeframe, parse_date(args.start), parse_date(args.end), __import__("pathlib").Path("reports/data_quality"))
                results.append(result)
        __import__("pathlib").Path("reports").mkdir(exist_ok=True)
        __import__("pathlib").Path("reports/ingestion_latest.json").write_text(json.dumps(results, default=str, indent=2), encoding="utf-8")
        print(json.dumps(results, default=str, indent=2))
    finally:
        manager.disconnect()


if __name__ == "__main__": main()
