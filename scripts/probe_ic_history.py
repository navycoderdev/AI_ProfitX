"""Read-only available BTCUSD broker history coverage, without ingesting."""

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import get_settings
from database.audit import AuditRepository
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager, _mapping
from mt5.services import MT5MarketDataService, MT5SymbolService


TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4")


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("History probe requires LIVE disabled.")
    sessions = initialize_database(settings)
    terminal_settings = settings.model_copy(update={"mt5_login": None, "mt5_password": None, "mt5_server": None})
    manager = MT5ConnectionManager(terminal_settings, AuditRepository(sessions))
    try:
        manager.connect()
        account = _mapping(manager.client.account_info())
        if account.get("server") != "ICMarketsSC-Demo" or account.get("trade_mode") != manager.client.ACCOUNT_TRADE_MODE_DEMO:
            raise RuntimeError("IC Markets demo broker identity is required.")
        service = MT5SymbolService(manager, terminal_settings, manager.audit)
        service.ensure_available("BTCUSD")
        market = MT5MarketDataService(manager, terminal_settings, manager.audit)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=60)
        rows = {}
        for tf in TIMEFRAMES:
            try:
                bars = market.rates("BTCUSD", service.supported_timeframes()[tf], start, end)
                rows[tf] = {"count": len(bars),
                            "first": datetime.fromtimestamp(bars[0]["time"], timezone.utc).isoformat() if bars else None,
                            "last": datetime.fromtimestamp(bars[-1]["time"], timezone.utc).isoformat() if bars else None}
                seconds = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}[tf]
                gaps = [(left["time"], right["time"], right["time"] - left["time"])
                        for left, right in zip(bars, bars[1:]) if right["time"] - left["time"] > seconds]
                rows[tf]["gaps"] = len(gaps)
                rows[tf]["gap_duration_seconds"] = dict(Counter(gap[2] for gap in gaps))
                rows[tf]["gap_start_utc"] = dict(Counter(datetime.fromtimestamp(gap[0], timezone.utc).strftime("%H:%M")
                                                    for gap in gaps).most_common(8))
                rows[tf]["gap_examples"] = [{"after": datetime.fromtimestamp(a, timezone.utc).isoformat(),
                                               "before": datetime.fromtimestamp(b, timezone.utc).isoformat(),
                                               "seconds": d} for a, b, d in gaps[:5]]
            except Exception:
                rows[tf] = {"count": 0, "error_code": manager.last_error()[0] if manager.last_error() else None}
        result = {"broker_server": "ICMarketsSC-Demo", "symbol": "BTCUSD", "requested_start": start.isoformat(),
                  "requested_end": end.isoformat(), "timeframes": rows, "orders_submitted": 0}
        Path("reports/ic_btcusd_history_probe.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        if manager.connected:
            manager.disconnect()


if __name__ == "__main__":
    main()
