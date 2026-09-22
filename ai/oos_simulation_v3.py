"""Offline six-symbol Brain-v3 OOS simulation with verified economics."""

SYMBOLS = {"EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "ETHUSD"}


def simulate_locked_oos_v3(model, rows, *, threshold, contracts):
    if model.version != "Brain-v3" or set(contracts) != SYMBOLS:
        raise ValueError("Brain-v3 and verified six-symbol contracts are required.")
    from collections import Counter, defaultdict
    from ai.oos_simulation import _summary
    from ai.types import ProposalAction
    if not rows or any(row["split"] != "OOS" or row["symbol"] not in contracts for row in rows):
        raise ValueError("Only frozen six-symbol OOS rows can be simulated.")
    predictions, symbol_predictions, skipped, unfilled = Counter(), defaultdict(Counter), Counter(), Counter()
    trades, last_exit = [], {}
    for row in sorted(rows, key=lambda item: (item["timestamp"], item["symbol"])):
        probability = model.probabilities(row["features"])
        action = max(probability, key=probability.get)
        if action is not ProposalAction.NO_TRADE and probability[action] < threshold:
            action = ProposalAction.NO_TRADE
        symbol = row["symbol"]; predictions[action.value] += 1; symbol_predictions[symbol][action.value] += 1
        if action is ProposalAction.NO_TRADE: continue
        if symbol in last_exit and row["timestamp"] <= last_exit[symbol]: skipped[symbol] += 1; continue
        entry, exit_price, spread = row["entry_open"], row["exit_close"], row["entry_spread_points"]
        if entry is None or spread is None: unfilled[symbol] += 1; continue
        if exit_price is None or row["timestamp"] >= row["target_raw_data_cutoff"]:
            raise ValueError(f"Missing or invalid genuine execution observations for {symbol}.")
        pnl = contracts[symbol].evaluate(action.value, entry, exit_price, spread)
        trades.append({"symbol": symbol, "action": action.value, "entry_at": row["timestamp"].isoformat(),
            "exit_at": row["target_raw_data_cutoff"].isoformat(), "entry_open": entry,
            "exit_close": exit_price, "observed_spread_points": spread,
            "gross_pnl_usd": pnl["gross_pnl_usd"], "transaction_costs_usd": pnl["spread_cost_usd"],
            "slippage_usd": pnl["slippage_usd"], "net_pnl_usd": pnl["net_pnl_usd"]})
        last_exit[symbol] = row["target_raw_data_cutoff"]
    result = _summary(trades, predictions, sum(skipped.values()))
    result["per_symbol"] = {symbol: {**_summary([trade for trade in trades if trade["symbol"] == symbol],
        symbol_predictions[symbol], skipped[symbol]), "unfilled_no_entry_candle": unfilled[symbol],
        "coverage_status": "AVAILABLE" if symbol_predictions[symbol] else "NO_OOS_ROWS"} for symbol in contracts}
    result["unfilled_no_entry_candle"] = sum(unfilled.values())
    result["trade_scope"] = "OFFLINE_OOS_SIMULATION_TRADES"
    result["execution_model"] = "next_M5_open_to_label_horizon_close_one_position_per_symbol"
    result["cost_assumptions"] = {"lots": {symbol: contract.lots for symbol, contract in contracts.items()},
        "spread": "observed_entry_M5_spread_points", "slippage": "one_verified_tick_each_side",
        "pnl": "symbol_contract_size_times_lots_with_profit_currency_conversion",
        "commission": 0.0, "max_drawdown_basis": "closed_trade_equity_starting_10000_usd"}
    return result
