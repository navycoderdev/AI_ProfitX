"""Offline evaluation of the locked Brain-v1 artifact. No execution gateway is imported."""
from collections import Counter, defaultdict
from datetime import datetime, timezone

from ai.types import ProposalAction


SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY")
STARTING_CASH_USD = 10_000.0
POSITION_UNITS = 1_000.0


def _summary(trades: list[dict], predictions: Counter, skipped: int) -> dict:
    equity = STARTING_CASH_USD
    peak = equity
    drawdown = 0.0
    for trade in sorted(trades, key=lambda item: (item["exit_at"], item["symbol"])):
        equity += trade["net_pnl_usd"]
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    wins = sum(trade["net_pnl_usd"] > 0 for trade in trades)
    losses = sum(trade["net_pnl_usd"] < 0 for trade in trades)
    profits = sum(max(trade["net_pnl_usd"], 0) for trade in trades)
    loss_amount = -sum(min(trade["net_pnl_usd"], 0) for trade in trades)
    return {
        "prediction_count": sum(predictions.values()),
        "prediction_counts": {label: predictions[label] for label in ("LONG", "SHORT", "NO_TRADE")},
        "simulated_trade_count": len(trades), "wins": wins, "losses": losses,
        "breakeven": len(trades) - wins - losses, "skipped_overlapping_signals": skipped,
        "gross_pnl_usd": sum(trade["gross_pnl_usd"] for trade in trades),
        "transaction_costs_usd": sum(trade["transaction_costs_usd"] for trade in trades),
        "slippage_usd": sum(trade["slippage_usd"] for trade in trades),
        "net_pnl_usd": sum(trade["net_pnl_usd"] for trade in trades),
        "profit_factor": profits / loss_amount if loss_amount else None,
        "max_drawdown": drawdown,
    }


def simulate_locked_oos(model, rows: list[dict], *, threshold: float, point_size: float,
                        spread_points: float, slippage_points_each_side: float) -> dict:
    """Enter at next M5 open, exit at label-horizon close; one position per symbol."""
    if model.version != "Brain-v1" or threshold != .40:
        raise ValueError("Only locked Brain-v1 at confidence threshold 0.40 is allowed.")
    if not rows or not {row["symbol"] for row in rows}.issubset(set(SYMBOLS)) or any(row["split"] != "OOS" for row in rows):
        raise ValueError("Simulation requires frozen OOS rows from the declared symbol universe.")
    predictions = Counter()
    symbol_predictions = defaultdict(Counter)
    trades: list[dict] = []
    last_exit = {}
    skipped = Counter()
    for row in sorted(rows, key=lambda item: (item["timestamp"], item["symbol"])):
        probabilities = model.probabilities(row["features"])
        action = max(probabilities, key=probabilities.get)
        if action is not ProposalAction.NO_TRADE and probabilities[action] < threshold:
            action = ProposalAction.NO_TRADE
        predictions[action.value] += 1
        symbol_predictions[row["symbol"]][action.value] += 1
        if action is ProposalAction.NO_TRADE:
            continue
        symbol = row["symbol"]
        if last_exit.get(symbol) and row["timestamp"] <= last_exit[symbol]:
            skipped[symbol] += 1
            continue
        entry, exit_price = row["entry_open"], row["exit_close"]
        if entry is None or exit_price is None or entry <= 0 or exit_price <= 0:
            raise ValueError(f"Missing genuine entry/exit market price for {symbol} at {row['timestamp']}.")
        if row["timestamp"] >= row["target_raw_data_cutoff"]:
            raise ValueError("Exit must follow the decision timestamp.")
        direction = 1 if action is ProposalAction.LONG else -1
        # EURUSD/GBPUSD quote currency is USD. USDJPY quote P&L is converted at exit.
        usd_conversion = 1.0 / exit_price if symbol == "USDJPY" else 1.0
        gross = direction * (exit_price - entry) * POSITION_UNITS * usd_conversion
        transaction = spread_points * point_size * POSITION_UNITS * usd_conversion
        slippage = 2 * slippage_points_each_side * point_size * POSITION_UNITS * usd_conversion
        trades.append({"symbol": symbol, "action": action.value, "entry_at": row["timestamp"].isoformat(),
                       "exit_at": row["target_raw_data_cutoff"].isoformat(), "entry_open": entry,
                       "exit_close": exit_price, "gross_pnl_usd": gross, "transaction_costs_usd": transaction,
                       "slippage_usd": slippage, "net_pnl_usd": gross - transaction - slippage})
        last_exit[symbol] = row["target_raw_data_cutoff"]
    by_symbol = {symbol: {**_summary([trade for trade in trades if trade["symbol"] == symbol],
                                  symbol_predictions[symbol], skipped[symbol]),
                          "coverage_status": "AVAILABLE" if symbol_predictions[symbol] else "NO_OOS_ROWS"} for symbol in SYMBOLS}
    result = _summary(trades, predictions, sum(skipped.values()))
    result["per_symbol"] = by_symbol
    result["trade_scope"] = "OFFLINE_OOS_SIMULATION_TRADES"
    result["execution_model"] = "next_M5_open_to_label_horizon_close_one_position_per_symbol"
    result["cost_assumptions"] = {"position_units": POSITION_UNITS, "point_size": point_size,
                                  "spread_points": spread_points, "slippage_points_each_side": slippage_points_each_side,
                                  "usd_jpy_conversion": "exit_close", "commission": 0.0,
                                  "max_drawdown_basis": "closed_trade_equity_starting_10000_usd"}
    return result
