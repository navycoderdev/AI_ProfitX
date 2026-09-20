"""Sanitized, read-only broker and crypto-contract onboarding probe."""

import json
from datetime import datetime, timezone
from pathlib import Path

from config.settings import get_settings
from data.operations import resolve_broker_symbol
from database.audit import AuditRepository
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager, _mapping
from mt5.services import MT5SymbolService


SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "ETHUSD")
CONTRACT_FIELDS = (
    "name", "description", "path", "digits", "point", "trade_tick_size",
    "trade_tick_value", "trade_tick_value_profit", "trade_tick_value_loss",
    "trade_contract_size", "currency_base", "currency_profit", "currency_margin",
    "volume_min", "volume_max", "volume_step", "trade_mode", "trade_calc_mode",
    "spread", "spread_float", "session_deals", "session_buy_orders", "session_sell_orders",
)


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Read-only broker probe requires LIVE disabled.")
    sessions = initialize_database(settings)
    # Probe the active terminal session. Local credentials may refer to an older account.
    connection_settings = settings.model_copy(update={"mt5_login": None, "mt5_password": None,
                                                   "mt5_server": None})
    manager = MT5ConnectionManager(connection_settings, AuditRepository(sessions))
    try:
        manager.connect()
        mt5 = manager.client
        account = _mapping(mt5.account_info())
        terminal = _mapping(mt5.terminal_info())
        if not account or not terminal:
            raise RuntimeError("Broker account or terminal metadata unavailable.")
        broker = {key: account.get(key) for key in ("company", "server", "currency", "trade_mode")}
        broker["terminal_connected"] = terminal.get("connected")
        broker["terminal_trade_allowed"] = terminal.get("trade_allowed")
        service = MT5SymbolService(manager, settings, manager.audit)
        mapping = {}
        for canonical in SYMBOLS:
            try:
                actual = resolve_broker_symbol(service, canonical)
                info = service.info(actual)
                mapping[canonical] = {"broker_symbol": actual,
                                      "metadata": {key: info.get(key) for key in CONTRACT_FIELDS}}
                if canonical in ("BTCUSD", "ETHUSD"):
                    tick = _mapping(mt5.symbol_info_tick(actual))
                    price = float(tick.get("ask") or 0)
                    size = float(info.get("trade_tick_size") or 0)
                    volume = float(info.get("volume_min") or 0)
                    if price > 0 and size > 0 and volume > 0:
                        mapping[canonical]["read_only_calculations"] = {
                            "sample_volume": volume, "sample_ask": price,
                            "buy_profit_up_one_tick": mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, actual, volume, price, price + size),
                            "sell_profit_down_one_tick": mt5.order_calc_profit(mt5.ORDER_TYPE_SELL, actual, volume, price, price - size),
                            "buy_margin": mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, actual, volume, price),
                            "tick_time": tick.get("time"), "bid": tick.get("bid"), "ask": tick.get("ask"),
                        }
            except Exception as exc:
                mapping[canonical] = {"broker_symbol": None, "reason": type(exc).__name__}
        ic_markets_demo = "ic" in str(broker.get("server", "")).lower() and "demo" in str(broker.get("server", "")).lower()
        result = {"observed_at": datetime.now(timezone.utc).isoformat(),
                  "broker": broker, "symbol_mapping": mapping,
                  "source": "MT5_account_symbol_info_and_read_only_calculations",
                  "broker_gate": "PASS" if ic_markets_demo else "BLOCKED_WRONG_ACCOUNT",
                  "btc_gate": "NOT_EVALUATED_WRONG_ACCOUNT" if not ic_markets_demo else
                              ("SYMBOL_UNAVAILABLE" if mapping["BTCUSD"]["broker_symbol"] is None else "PENDING_CONTRACT_AUDIT"),
                  "credentials_in_report": False, "orders_submitted": 0}
        path = Path("reports/broker_btcusd_probe.json")
        path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        if manager.connected:
            manager.disconnect()


if __name__ == "__main__":
    main()
