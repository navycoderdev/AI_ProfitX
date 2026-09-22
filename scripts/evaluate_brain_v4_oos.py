"""Single-use locked Brain-v4 Dataset-v4 OOS evaluation."""

import json
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path

from ai.brain_v1 import BrainV1Dataset
from ai.model import InterpretableLinearModel
from ai.types import ProposalAction
from backtesting.symbol_economics import SymbolEconomics
from config.settings import get_settings
from database.session import initialize_database
from research.brain_v4 import FAMILIES, enriched_features, head_key, normalized_spread_gate, stop_target

CONFIG_HASH = "336d0a442fe8b829b6b8ad122e2cc141ef8fb723220a414afa9a5d1b30678172"
DATASET_HASH = "8e0d2fa19312a7b85134f72b8f3728a034b8dde1fb737bf867817c071e57932c"
OUTPUT = Path("reports/brain_v4_oos_evaluation.json")


def load_locked():
    payload = json.loads(Path("reports/brain_v4_pre_oos_config.json").read_text(encoding="utf-8"))
    claimed = payload.pop("content_hash")
    actual = sha256(json.dumps(payload, indent=2, sort_keys=True).encode()).hexdigest()
    if claimed != CONFIG_HASH or actual != CONFIG_HASH or payload["state"] != "FROZEN_PRE_OOS" or payload["oos_accessed"]:
        raise RuntimeError("Brain-v4 pre-OOS configuration integrity gate failed.")
    return payload


def model_from(payload):
    return InterpretableLinearModel(payload["version"], tuple(payload["feature_names"]), payload["means"],
                                    payload["scales"], payload["weights"], payload.get("temperature", 1.0))


def confidence_bucket(value):
    return "[0.33,0.40)" if value < .4 else "[0.40,0.50)" if value < .5 else "[0.50,0.60)" if value < .6 else "[0.60,1.00]"


def regime(values):
    trend = values["trend_alignment"]
    direction = "UP" if trend >= .5 else "DOWN" if trend <= -.5 else "MIXED"
    vol = values["volatility_percentile"]
    return f"{direction}_{'LOW_VOL' if vol < .33 else 'MID_VOL' if vol < .67 else 'HIGH_VOL'}"


def summarize(trades, predictions):
    wins = [t for t in trades if t["net_pnl_usd"] > 0]; losses = [t for t in trades if t["net_pnl_usd"] < 0]
    gross_profit = sum(t["net_pnl_usd"] for t in wins); gross_loss = abs(sum(t["net_pnl_usd"] for t in losses))
    equity = peak = 10000.; max_dd = 0.
    for trade in trades:
        equity += trade["net_pnl_usd"]; peak = max(peak, equity); max_dd = max(max_dd, (peak-equity)/peak)
    return {"predictions": sum(predictions.values()), "prediction_counts": dict(predictions), "trades": len(trades),
        "wins": len(wins), "losses": len(losses), "gross_pnl_usd": sum(t["gross_pnl_usd"] for t in trades),
        "costs_usd": sum(t["transaction_costs_usd"] for t in trades),
        "slippage_usd": sum(t["slippage_usd"] for t in trades),
        "net_pnl_usd": sum(t["net_pnl_usd"] for t in trades),
        "profit_factor": gross_profit/gross_loss if gross_loss else None, "max_drawdown": max_dd}


def main():
    if OUTPUT.exists(): raise RuntimeError("Immutable Brain-v4 OOS evidence already exists; refusing a second opening.")
    locked = load_locked(); settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE": raise RuntimeError("Offline OOS requires LIVE disabled.")
    contracts_raw = json.loads(Path("reports/ic_contract_economics.json").read_text(encoding="utf-8"))["symbols"]
    economics = {s: SymbolEconomics.from_mt5_probe(s, v) for s, v in contracts_raw.items()}
    models = {key: model_from(value) for key, value in locked["models"].items()}
    # The frozen dataset is loaded once and the OOS partition is materialized once in this single-use process.
    all_rows, _, _ = BrainV1Dataset(initialize_database(settings), DATASET_HASH, "research-dataset-v4").rows()
    oos = [row for row in all_rows if row["split"] == "OOS"]
    threshold = locked["confidence_threshold"]
    predictions, confidence, regimes, raw_records = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter), []
    trades, last_exit, risk_checks = [], {}, Counter()
    for row in sorted(oos, key=lambda item: (item["timestamp"], item["symbol"])):
        symbol = row["symbol"]; values = enriched_features(row, contracts_raw)
        probs = models[head_key(symbol, locked["architecture"])].probabilities(values)
        raw = max(probs, key=probs.get); conf = probs[raw]
        action = ProposalAction.NO_TRADE if raw is not ProposalAction.NO_TRADE and conf < threshold else raw
        predictions["ALL"][action.value] += 1; predictions[symbol][action.value] += 1; predictions[FAMILIES[symbol]][action.value] += 1
        correct = action.value == row["target"]
        for scope in ("ALL", FAMILIES[symbol], symbol):
            confidence[scope][f"{confidence_bucket(conf)}|{'CORRECT' if correct else 'WRONG'}"] += 1
            regimes[scope][f"{regime(values)}|{'CORRECT' if correct else 'WRONG'}"] += 1
        raw_records.append((symbol, action.value, conf, regime(values), correct))
        if action is ProposalAction.NO_TRADE: continue
        proposal = stop_target(symbol, action.value, row["entry_open"] or row["features"]["rolling_high_20"],
                               row["features"]["atr_14"], contracts_raw[symbol])
        gate = normalized_spread_gate(symbol, row["entry_spread_points"] or 0, row["features"]["atr_14"],
                                      proposal["entry"], 10000., contracts_raw[symbol])
        valid_stop_target = (proposal["risk_reward"] + 1e-9 >= proposal["policy"]["minimum_risk_reward"] and
            (action is ProposalAction.LONG and proposal["stop_loss"] < proposal["entry"] < proposal["take_profit"] or
             action is ProposalAction.SHORT and proposal["take_profit"] < proposal["entry"] < proposal["stop_loss"]))
        risk_checks["STOP_TARGET_VALID" if valid_stop_target else "STOP_TARGET_INVALID"] += 1
        risk_checks["SPREAD_PASS" if gate["approved"] else "SPREAD_BLOCK"] += 1
        if symbol in last_exit and row["timestamp"] <= last_exit[symbol]: continue
        if row["entry_open"] is None or row["entry_spread_points"] is None or row["exit_close"] is None: continue
        pnl = economics[symbol].evaluate(action.value, row["entry_open"], row["exit_close"], row["entry_spread_points"])
        trades.append({"symbol": symbol, "family": FAMILIES[symbol], "action": action.value,
            "gross_pnl_usd": pnl["gross_pnl_usd"], "transaction_costs_usd": pnl["spread_cost_usd"],
            "slippage_usd": pnl["slippage_usd"], "net_pnl_usd": pnl["net_pnl_usd"]})
        last_exit[symbol] = row["target_raw_data_cutoff"]
    scopes = {"ALL": list(FAMILIES), **{f: [s for s in FAMILIES if FAMILIES[s] == f] for f in set(FAMILIES.values())},
              **{s: [s] for s in FAMILIES}}
    summaries = {scope: summarize([t for t in trades if t["symbol"] in symbols], predictions[scope]) for scope, symbols in scopes.items()}
    brain3 = json.loads(Path("reports/brain_v3_oos_simulation.json").read_text(encoding="utf-8"))
    comparison = {"Brain-v3": {"trades": brain3["simulated_trade_count"], "gross_pnl_usd": brain3["gross_pnl_usd"],
        "net_pnl_usd": brain3["net_pnl_usd"], "profit_factor": brain3["profit_factor"], "max_drawdown": brain3["max_drawdown"]},
        "Brain-v4": summaries["ALL"]}
    result = {"model_version": "Brain-v4", "status": "RESEARCH_ONLY", "frozen_config_hash": CONFIG_HASH,
        "dataset_hash": DATASET_HASH, "oos_open_count": 1, "retuned": False, "architecture": locked["architecture"],
        "confidence_threshold": threshold, "results": summaries,
        "confidence_performance": {scope: dict(values) for scope, values in confidence.items()},
        "regime_performance": {scope: dict(values) for scope, values in regimes.items()},
        "risk_stop_target": dict(risk_checks),
        "brain_v3_comparison_same_oos": comparison,
        "safety": {"orders": 0, "paper": 0, "live": False},
        "go_no_go": "NO_GO" if summaries["ALL"]["net_pnl_usd"] <= 0 else "TECHNICAL_REVIEW_REQUIRED"}
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"overall": summaries["ALL"], "go_no_go": result["go_no_go"]}, indent=2))


if __name__ == "__main__": main()
