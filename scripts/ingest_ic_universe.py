"""Append genuine closed IC Markets history for the Dataset-v4 candidate universe."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import get_settings
from data.candle_builder import TIMEFRAME_SECONDS
from data.normalizer import DataNormalizer
from data.storage import RawMarketDataRepository
from database.audit import AuditRepository
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager, _mapping
from mt5.services import MT5MarketDataService, MT5SymbolService


SOURCE = "MT5:ICMarketsSC-Demo"
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "ETHUSD")
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4")


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Historical research requires LIVE disabled.")
    sessions = initialize_database(settings)
    terminal_settings = settings.model_copy(update={"mt5_login": None, "mt5_password": None, "mt5_server": None})
    manager = MT5ConnectionManager(terminal_settings, AuditRepository(sessions))
    try:
        manager.connect()
        account = _mapping(manager.client.account_info())
        if account.get("server") != "ICMarketsSC-Demo" or account.get("trade_mode") != manager.client.ACCOUNT_TRADE_MODE_DEMO:
            raise RuntimeError("IC Markets demo identity changed; ingestion blocked.")
        symbol_service = MT5SymbolService(manager, terminal_settings, manager.audit)
        market = MT5MarketDataService(manager, terminal_settings, manager.audit)
        repository, normalizer = RawMarketDataRepository(sessions), DataNormalizer()
        end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        start = end - timedelta(days=60)
        result = {"broker_server": account.get("server"), "source": SOURCE,
                  "requested_start": start.isoformat(), "requested_end": end.isoformat(),
                  "symbols": {}, "orders_submitted": 0}
        for symbol in SYMBOLS:
            info = symbol_service.ensure_available(symbol)
            result["symbols"][symbol] = {"broker_symbol": info.get("name"), "timeframes": {}}
            for tf in TIMEFRAMES:
                cursor = start
                received = inserted = 0
                first = last = None
                while cursor < end:
                    edge = min(cursor + timedelta(days=15), end)
                    payloads = market.rates(symbol, symbol_service.supported_timeframes()[tf], cursor, edge)
                    rows = [normalizer.bar(SOURCE, symbol, tf, payload) for payload in payloads]
                    rows = [row for row in rows if cursor <= row.timestamp < edge and
                            row.timestamp + timedelta(seconds=TIMEFRAME_SECONDS[tf]) <= end]
                    for offset in range(0, len(rows), 500):
                        inserted += repository.append_bars_bulk(rows[offset:offset + 500])
                    received += len(rows)
                    if rows:
                        first = min(first, rows[0].timestamp) if first else rows[0].timestamp
                        last = max(last, rows[-1].timestamp) if last else rows[-1].timestamp
                    cursor = edge
                result["symbols"][symbol]["timeframes"][tf] = {
                    "received": received, "inserted": inserted, "duplicates_skipped": received - inserted,
                    "first": first.isoformat() if first else None, "last": last.isoformat() if last else None}
        Path("reports/ic_universe_ingestion.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        if manager.connected:
            manager.disconnect()


if __name__ == "__main__":
    main()
