"""Read-only MT5 symbol mapping and contract metadata probe for Dataset v3."""
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from config.settings import get_settings
from data.operations import resolve_broker_symbol
from database.audit import AuditRepository
from database.models import RawMarketBar
from database.session import initialize_database
from mt5.connection import MT5ConnectionManager, _mapping
from mt5.services import MT5SymbolService


SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "ETHUSD")
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4")
FIELDS = ("point", "digits", "trade_tick_size", "trade_tick_value", "trade_contract_size",
          "spread", "spread_float", "currency_profit", "trade_mode")


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Research probe requires LIVE permission disabled.")
    sessions = initialize_database(settings)
    manager = MT5ConnectionManager(settings, AuditRepository(sessions))
    try:
        manager.connect()
        service = MT5SymbolService(manager, settings, manager.audit)
        catalogue = [_mapping(item) for item in (service.mt5.symbols_get() or ())]
        candidates = [item.get("name") for item in catalogue if any(token in
            (str(item.get("name", "")) + " " + str(item.get("description", ""))).upper()
            for token in ("BTC", "ETH", "BITCOIN", "ETHEREUM", "CRYPTO"))]
        direct_crypto_candidates = {}
        for name in ("BTC", "ETH"):
            info = _mapping(service.mt5.symbol_info(name))
            if info:
                direct_crypto_candidates[name] = {key: info.get(key) for key in
                    ("name", "description", "path", "currency_base", "currency_profit", "point",
                     "trade_tick_size", "trade_tick_value", "trade_contract_size", "trade_mode")}
        mapping = {}
        for canonical in SYMBOLS:
            try:
                broker = resolve_broker_symbol(service, canonical)
                info = service.info(broker)
                mapping[canonical] = {"canonical_symbol": canonical, "broker_symbol": broker,
                                     "metadata": {key: info.get(key) for key in FIELDS}}
            except Exception as exc:
                mapping[canonical] = {"canonical_symbol": canonical, "broker_symbol": None, "error": str(exc)}
        with sessions() as session:
            raw = session.execute(select(RawMarketBar.symbol, RawMarketBar.timeframe, func.count(RawMarketBar.id),
                func.min(RawMarketBar.timestamp), func.max(RawMarketBar.timestamp)).where(
                RawMarketBar.symbol.in_(SYMBOLS), RawMarketBar.timeframe.in_(TIMEFRAMES)).group_by(
                RawMarketBar.symbol, RawMarketBar.timeframe)).all()
        local = {(symbol, timeframe): {"count": count, "start": start.isoformat(), "end": end.isoformat()}
                 for symbol, timeframe, count, start, end in raw}
        coverage = {symbol: {timeframe: local.get((symbol, timeframe), {"count": 0, "start": None, "end": None})
                             for timeframe in TIMEFRAMES} for symbol in SYMBOLS}
        blocked = [symbol for symbol in SYMBOLS if mapping[symbol]["broker_symbol"] is None]
        result = {"probed_at": datetime.now(timezone.utc).isoformat(), "source": "connected_MT5_terminal",
                  "broker_catalogue_size": len(catalogue), "crypto_catalogue_candidates": sorted(set(candidates)),
                  "direct_crypto_candidates": direct_crypto_candidates,
                  "broker_symbol_mapping": mapping, "local_raw_coverage": coverage,
                  "stop_before_dataset_v3_and_brain_v2": bool(blocked),
                  "blocking_symbols": blocked,
                  "dataset_v3_state": "NOT_STARTED", "brain_v2_state": "NOT_STARTED"}
        path = Path("reports/market_universe_probe.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        if manager.connected:
            manager.disconnect()


if __name__ == "__main__":
    main()
