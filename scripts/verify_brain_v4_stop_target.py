"""Replay only frozen Brain-v4 OOS stop/target arithmetic; never rescore economics."""

import json
import sys
from collections import Counter
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai.brain_v1 import BrainV1Dataset
from ai.model import InterpretableLinearModel
from ai.types import ProposalAction
from config.settings import get_settings
from database.session import initialize_database
from research.brain_v4 import enriched_features, head_key, stop_target

CONFIG_HASH = "336d0a442fe8b829b6b8ad122e2cc141ef8fb723220a414afa9a5d1b30678172"
DATASET_HASH = "8e0d2fa19312a7b85134f72b8f3728a034b8dde1fb737bf867817c071e57932c"


def main():
    config = json.loads(Path("reports/brain_v4_pre_oos_config.json").read_text(encoding="utf-8"))
    claimed = config.pop("content_hash")
    if claimed != CONFIG_HASH or sha256(json.dumps(config, indent=2, sort_keys=True).encode()).hexdigest() != CONFIG_HASH:
        raise RuntimeError("Frozen Brain-v4 config hash mismatch.")
    contracts = json.loads(Path("reports/ic_contract_economics.json").read_text(encoding="utf-8"))["symbols"]
    models = {key: InterpretableLinearModel(value["version"], tuple(value["feature_names"]), value["means"],
        value["scales"], value["weights"], value.get("temperature", 1.0)) for key, value in config["models"].items()}
    rows, _, _ = BrainV1Dataset(initialize_database(get_settings()), DATASET_HASH, "research-dataset-v4").rows()
    counts, causes, examples = Counter(), Counter(), []
    for row in (item for item in rows if item["split"] == "OOS"):
        symbol = row["symbol"]; values = enriched_features(row, contracts)
        probabilities = models[head_key(symbol, config["architecture"])].probabilities(values)
        action = max(probabilities, key=probabilities.get)
        if action is not ProposalAction.NO_TRADE and probabilities[action] < config["confidence_threshold"]:
            action = ProposalAction.NO_TRADE
        if action is ProposalAction.NO_TRADE: continue
        proposal = stop_target(symbol, action.value, row["entry_open"] or row["features"]["rolling_high_20"],
            row["features"]["atr_14"], contracts[symbol])
        counts[(symbol, "DIRECTIONAL")] += 1
        strict = proposal["risk_reward"] >= proposal["policy"]["minimum_risk_reward"]
        side_ok = (action is ProposalAction.LONG and proposal["stop_loss"] < proposal["entry"] < proposal["take_profit"] or
                   action is ProposalAction.SHORT and proposal["take_profit"] < proposal["entry"] < proposal["stop_loss"])
        tick = Decimal(str(contracts[symbol]["tick_size"]))
        tick_ok = all(Decimal(str(proposal[key])) % tick == 0 for key in ("stop_loss", "take_profit"))
        tolerant = proposal["risk_reward"] + 1e-12 >= proposal["policy"]["minimum_risk_reward"]
        counts[(symbol, "STRICT_PASS")] += int(strict and side_ok and tick_ok)
        counts[(symbol, "TOLERANT_PASS")] += int(tolerant and side_ok and tick_ok)
        if not strict:
            causes["BINARY_FLOAT_RATIO_BELOW_EXACT_1_5"] += 1
            if len(examples) < 5: examples.append({"symbol": symbol, "risk_reward": proposal["risk_reward"],
                "minimum": proposal["policy"]["minimum_risk_reward"], "difference": proposal["risk_reward"] - 1.5})
        if not side_ok: causes["INVALID_SIDE"] += 1
        if not tick_ok: causes["INVALID_TICK_ALIGNMENT"] += 1
    total = lambda label: sum(value for (symbol, key), value in counts.items() if key == label)
    result = {"frozen_config_hash": CONFIG_HASH, "verification_only": True, "model_retuned": False,
        "directional_proposals": total("DIRECTIONAL"), "original_strict_pass": total("STRICT_PASS"),
        "resolved_pass_with_numeric_tolerance": total("TOLERANT_PASS"),
        "remaining_failures": total("DIRECTIONAL") - total("TOLERANT_PASS"),
        "root_causes": dict(causes), "examples": examples,
        "per_symbol": {symbol: {key: counts[(symbol, key)] for key in ("DIRECTIONAL", "STRICT_PASS", "TOLERANT_PASS")}
                       for symbol in contracts},
        "policy": "IEEE-754 comparison tolerance 1e-12; no model, threshold, ATR multiplier, tick rounding, or risk-policy change."}
    Path("reports/brain_v4_stop_target_verification.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__": main()
