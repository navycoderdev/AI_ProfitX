"""Read-only MT5 profit-calculator cross-check for candidate research symbols."""
import json
from datetime import datetime, timezone
from pathlib import Path

from config.settings import get_settings
from database.audit import AuditRepository
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager, _mapping
from mt5.services import MT5SymbolService


SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD")


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Contract probe requires LIVE disabled.")
    sessions = initialize_database(settings)
    manager = MT5ConnectionManager(settings, AuditRepository(sessions))
    try:
        manager.connect()
        service = MT5SymbolService(manager, settings, manager.audit)
        result = {}
        account = _mapping(manager.client.account_info())
        for symbol in SYMBOLS:
            info = service.info(symbol)
            tick = _mapping(manager.client.symbol_info_tick(symbol))
            size = float(info["trade_tick_size"])
            price = float(tick["ask"])
            per_tick_buy = manager.client.order_calc_profit(manager.client.ORDER_TYPE_BUY, symbol, 1.0, price, price + size)
            per_tick_sell = manager.client.order_calc_profit(manager.client.ORDER_TYPE_SELL, symbol, 1.0, price, price - size)
            if per_tick_buy is None or per_tick_sell is None:
                raise RuntimeError(f"MT5 profit calculation unavailable for {symbol}: {manager.last_error()}")
            result[symbol] = {"canonical_symbol": symbol, "broker_symbol": symbol,
                "point": info.get("point"), "digits": info.get("digits"), "tick_size": size,
                "metadata_tick_value": info.get("trade_tick_value"), "contract_size": info.get("trade_contract_size"),
                "volume_min": info.get("volume_min"), "volume_step": info.get("volume_step"),
                "currency_profit": info.get("currency_profit"), "spread_points_snapshot": info.get("spread"),
                "spread_float": info.get("spread_float"), "quote_bid_snapshot": tick.get("bid"),
                "quote_ask_snapshot": tick.get("ask"), "mt5_profit_per_tick_buy_one_lot": per_tick_buy,
                "mt5_profit_per_tick_sell_one_lot": per_tick_sell}
        report = {"observed_at": datetime.now(timezone.utc).isoformat(),
                  "account_currency": account.get("currency"), "source": "MT5_symbol_info_and_order_calc_profit_read_only",
                  "symbols": result, "orders_submitted": 0}
        path = Path("reports/contract_economics.json")
        path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True))
    finally:
        if manager.connected:
            manager.disconnect()


if __name__ == "__main__":
    main()
