from backtesting.symbol_economics import SymbolEconomics


def contract(symbol, size, point, tick, currency, profit):
    return {"canonical_symbol": symbol, "broker_symbol": symbol, "contract_size": size,
            "point": point, "tick_size": tick, "currency_profit": currency,
            "volume_min": .01, "mt5_profit_per_tick_buy_one_lot": profit}


def test_eurusd_usd_account_contract_and_spread():
    model = SymbolEconomics.from_mt5_probe("EURUSD", contract("EURUSD", 100000, .00001, .00001, "USD", 1))
    result = model.evaluate("LONG", 1.10, 1.101, 3)
    assert round(result["gross_pnl_usd"], 6) == 1
    assert round(result["spread_cost_usd"], 6) == .03
    assert round(result["slippage_usd"], 6) == .02


def test_usdjpy_converts_jpy_quote_pnl_to_usd():
    model = SymbolEconomics.from_mt5_probe("USDJPY", contract("USDJPY", 100000, .001, .001, "JPY", .64))
    result = model.evaluate("SHORT", 156.90, 156.80, 4)
    assert abs(result["gross_pnl_usd"] - (100 / 156.80)) < 1e-9
    assert abs(result["spread_cost_usd"] - (4 / 156.80)) < 1e-9


def test_xauusd_uses_gold_contract_size_not_fx_pip():
    model = SymbolEconomics.from_mt5_probe("XAUUSD", contract("XAUUSD", 100, .01, .01, "USD", 1))
    result = model.evaluate("LONG", 4300, 4301, 60)
    assert result == {"gross_pnl_usd": 1, "spread_cost_usd": .6,
                      "slippage_usd": .02, "net_pnl_usd": .38}
