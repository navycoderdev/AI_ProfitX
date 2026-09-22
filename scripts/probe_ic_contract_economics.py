"""Read-only IC Markets contract economics for Dataset-v4 symbols."""

import json
from datetime import datetime, timezone
from pathlib import Path

from config.settings import get_settings
from database.audit import AuditRepository
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager, _mapping
from mt5.services import MT5SymbolService


SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "ETHUSD")


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Contract probe requires LIVE disabled.")
    sessions = initialize_database(settings)
    terminal_settings = settings.model_copy(update={"mt5_login": None, "mt5_password": None, "mt5_server": None})
    manager = MT5ConnectionManager(terminal_settings, AuditRepository(sessions))
    try:
        manager.connect()
        mt5 = manager.client
        account = _mapping(mt5.account_info())
        if account.get("server") != "ICMarketsSC-Demo" or account.get("trade_mode") != mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise RuntimeError("IC Markets demo identity is required.")
        service = MT5SymbolService(manager, terminal_settings, manager.audit)
        result = {}
        for symbol in SYMBOLS:
            info = service.ensure_available(symbol)
            tick = _mapping(mt5.symbol_info_tick(symbol))
            size, price = float(info["trade_tick_size"]), float(tick["ask"])
            buy = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol, 1.0, price, price + size)
            sell = mt5.order_calc_profit(mt5.ORDER_TYPE_SELL, symbol, 1.0, price, price - size)
            margin = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, float(info["volume_min"]), price)
            if buy is None or sell is None or margin is None:
                raise RuntimeError(f"Read-only economics calculation unavailable for {symbol}.")
            result[symbol] = {"canonical_symbol": symbol, "broker_symbol": info["name"],
                "point": info["point"], "digits": info["digits"], "tick_size": size,
                "metadata_tick_value": info["trade_tick_value"], "contract_size": info["trade_contract_size"],
                "volume_min": info["volume_min"], "volume_max": info["volume_max"], "volume_step": info["volume_step"],
                "currency_profit": info["currency_profit"], "spread_points_snapshot": info["spread"],
                "spread_float": info["spread_float"], "quote_bid_snapshot": tick.get("bid"),
                "quote_ask_snapshot": tick.get("ask"), "mt5_profit_per_tick_buy_one_lot": buy,
                "mt5_profit_per_tick_sell_one_lot": sell, "mt5_margin_min_volume": margin}
        report = {"observed_at": datetime.now(timezone.utc).isoformat(), "broker_server": account.get("server"),
            "account_currency": account.get("currency"), "source": "MT5:ICMarketsSC-Demo_read_only_calculations",
            "symbols": result, "orders_submitted": 0}
        Path("reports/ic_contract_economics.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True))
    finally:
        if manager.connected:
            manager.disconnect()


if __name__ == "__main__":
    main()
