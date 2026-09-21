"""Append genuine closed IC Markets BTCUSD history with broker lineage."""

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
        symbols = MT5SymbolService(manager, terminal_settings, manager.audit)
        info = symbols.ensure_available("BTCUSD")
        if "crypto" not in str(info.get("path", "")).lower() or "bitcoin" not in str(info.get("description", "")).lower():
            raise RuntimeError("BTCUSD instrument classification failed.")
        market = MT5MarketDataService(manager, terminal_settings, manager.audit)
        repository = RawMarketDataRepository(sessions)
        normalizer = DataNormalizer()
        end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        start = end - timedelta(days=60)
        result = {"broker_server": "ICMarketsSC-Demo", "source": SOURCE, "symbol": "BTCUSD",
                  "requested_start": start.isoformat(), "requested_end": end.isoformat(),
                  "timeframes": {}, "orders_submitted": 0}
        for tf in TIMEFRAMES:
            cursor = start
            received = inserted = 0
            first = last = None
            while cursor < end:
                edge = min(cursor + timedelta(days=15), end)
                payloads = market.rates("BTCUSD", symbols.supported_timeframes()[tf], cursor, edge)
                bars = [normalizer.bar(SOURCE, "BTCUSD", tf, row) for row in payloads]
                bars = [bar for bar in bars if cursor <= bar.timestamp < edge and
                        bar.timestamp + timedelta(seconds=TIMEFRAME_SECONDS[tf]) <= end]
                for offset in range(0, len(bars), 500):
                    inserted += repository.append_bars_bulk(bars[offset:offset + 500])
                received += len(bars)
                if bars:
                    first = min(first, bars[0].timestamp) if first else bars[0].timestamp
                    last = max(last, bars[-1].timestamp) if last else bars[-1].timestamp
                cursor = edge
            result["timeframes"][tf] = {"received": received, "inserted": inserted,
                                         "duplicates_skipped": received - inserted,
                                         "first": first.isoformat() if first else None,
                                         "last": last.isoformat() if last else None}
        path = Path("reports/ic_btcusd_ingestion.json")
        path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        if manager.connected:
            manager.disconnect()


if __name__ == "__main__":
    main()
