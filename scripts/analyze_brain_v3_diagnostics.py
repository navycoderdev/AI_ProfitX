"""Read-only Brain-v3 OOS and original 37-decision Shadow diagnostic."""

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ai.artifacts import ModelArtifactManager
from ai.brain_v1 import BrainV1Dataset
from ai.types import ProposalAction
from config.settings import get_settings
from database.models import FeatureSnapshot, ShadowDecision, ShadowOutcome
from database.session import initialize_database
from sqlalchemy import select

DATASET_HASH = "8e0d2fa19312a7b85134f72b8f3728a034b8dde1fb737bf867817c071e57932c"
ORIGINAL_LAST = datetime.fromisoformat("2026-09-22T00:15:00+00:00")


def confidence_bucket(value: float) -> str:
    if value < .40: return "[0.33,0.40)"
    if value < .50: return "[0.40,0.50)"
    if value < .60: return "[0.50,0.60)"
    return "[0.60,1.00]"


def regime(values: dict) -> str:
    trends = [values.get(key) for key in ("m15_trend", "m30_trend", "h1_trend", "h4_trend")]
    direction = "UP" if all(item == "UP" or item == 1 for item in trends) else "DOWN" if all(
        item == "DOWN" or item == -1 for item in trends) else "MIXED"
    vol = float(values.get("volatility_percentile", 0.0))
    return f"{direction}_{'LOW_VOL' if vol < .33 else 'MID_VOL' if vol < .67 else 'HIGH_VOL'}"


def main() -> None:
    settings = get_settings()
    sessions = initialize_database(settings)
    model, metadata = ModelArtifactManager().load("Brain-v3")
    rows, _, meta = BrainV1Dataset(sessions, DATASET_HASH, "research-dataset-v4").rows()
    oos = [row for row in rows if row["split"] == "OOS"]
    symbol_stats = {}
    for symbol in sorted({row["symbol"] for row in oos}):
        subset = [row for row in oos if row["symbol"] == symbol]
        actual, final, raw, buckets, action_correct, regimes = (Counter() for _ in range(6))
        for row in subset:
            probabilities = model.probabilities(row["features"])
            prediction = max(probabilities, key=probabilities.get)
            confidence = probabilities[prediction]
            decision = ProposalAction.NO_TRADE if prediction is not ProposalAction.NO_TRADE and confidence < .40 else prediction
            actual[row["target"]] += 1; raw[prediction.value] += 1; final[decision.value] += 1
            buckets[confidence_bucket(confidence)] += 1
            action_correct[f"{decision.value}|{'CORRECT' if decision.value == row['target'] else 'WRONG'}"] += 1
            regimes[f"{regime(row['features'])}|{decision.value}|{'CORRECT' if decision.value == row['target'] else 'WRONG'}"] += 1
        symbol_stats[symbol] = {"rows": len(subset), "class_distribution": dict(actual),
            "raw_prediction_distribution": dict(raw), "final_prediction_distribution": dict(final),
            "confidence_buckets": dict(buckets), "action_accuracy_counts": dict(action_correct),
            "regime_action_accuracy_counts": dict(regimes)}

    with sessions() as session:
        joined = session.execute(select(ShadowDecision, FeatureSnapshot, ShadowOutcome).join(
            FeatureSnapshot, FeatureSnapshot.snapshot_id == ShadowDecision.feature_snapshot_id).outerjoin(
            ShadowOutcome, ShadowOutcome.decision_id == ShadowDecision.decision_id).where(
            ShadowDecision.model_version == "Brain-v3", ShadowDecision.symbol == "BTCUSD",
            ShadowDecision.decision_at <= ORIGINAL_LAST.replace(tzinfo=None)).order_by(ShadowDecision.decision_at)).all()
    if len(joined) != 37:
        raise RuntimeError(f"Original audited Shadow cohort changed: expected 37, found {len(joined)}")
    shadow_rows, reasons, decisions, outcomes, shadow_regimes = [], Counter(), Counter(), Counter(), Counter()
    for decision, snapshot, outcome in joined:
        reasons.update(decision.risk_reason_codes); decisions[decision.final_decision] += 1
        outcomes["RESOLVED" if outcome else "PENDING"] += 1; shadow_regimes[regime(snapshot.values)] += 1
        shadow_rows.append({"decision_at": decision.decision_at.isoformat(), "signal": decision.final_decision,
            "raw_prediction": decision.raw_prediction, "confidence": decision.confidence,
            "probabilities": decision.probabilities, "risk_status": decision.risk_status,
            "risk_reason_codes": decision.risk_reason_codes, "market_context": decision.market_context,
            "regime": regime(snapshot.values), "features": snapshot.values,
            "outcome": None if outcome is None else {"horizon_bar_at": outcome.horizon_bar_at.isoformat(),
                "realized_label": outcome.realized_label, "entry_close": outcome.entry_close,
                "horizon_close": outcome.horizon_close, "hypothetical_pnl_usd": outcome.hypothetical_pnl_usd}})
    simulation = json.loads(Path("reports/brain_v3_oos_simulation.json").read_text(encoding="utf-8"))
    continuation = json.loads(Path("reports/brain_v3_btc_shadow.json").read_text(encoding="utf-8"))
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "model_version": model.version,
        "artifact_hash": __import__('hashlib').sha256(Path("models/artifacts/Brain-v3.json").read_bytes()).hexdigest(),
        "dataset_hash": DATASET_HASH, "confidence_policy": metadata["confidence_policy"],
        "shadow_original_37": {"counts": dict(decisions), "risk_reasons": dict(reasons),
            "outcomes": dict(outcomes), "regimes": dict(shadow_regimes), "decisions": shadow_rows},
        "shadow_continuation": {"runtime_start": continuation["runtime_start"],
            "runtime_end": continuation["runtime_end"], "new_genuine_closed_m5_decisions": continuation["new_decisions"],
            "stop_reason": continuation["stop_reason"], "orders_submitted": continuation["orders_submitted"],
            "runtime_paper_trades": continuation["runtime_paper_trades"],
            "live_permission": continuation["live_permission"]},
        "risk_root_cause": {"classification": "EXPECTED_FAIL_CLOSED_PLUS_INTEGRATION_CONFIGURATION_GAP",
            "invalid_stop": "Shadow proposals intentionally provide no stop/target; hard-stop policy therefore rejects every directional proposal.",
            "max_spread": "BTCUSD observed spread points exceed the generic RiskProfile maximum_spread_points=30; no validated symbol-aware BTC risk profile is wired.",
            "policy_action": "Do not weaken either rule. Design and validate stop/target proposals and symbol-aware spread/economics on TRAIN/VALIDATION before any execution milestone."},
        "oos": {"rows": len(oos), "per_symbol": symbol_stats,
            "simulation": {"gross_pnl_usd": simulation["gross_pnl_usd"], "transaction_costs_usd": simulation["transaction_costs_usd"],
                "slippage_usd": simulation["slippage_usd"], "net_pnl_usd": simulation["net_pnl_usd"],
                "trades": simulation["simulated_trade_count"], "per_symbol": simulation["per_symbol"]}},
        "brain_v4_train_only_recommendation": [
            "Test symbol identity or asset-family heads because one pooled model receives no symbol feature despite heterogeneous label and price scales.",
            "Normalize price-, volatility-, spread-, and cost-related inputs by symbol economics; evaluate every ablation on TRAIN then lock with VALIDATION.",
            "Research explicit stop/target proposal generation and a validated symbol-aware risk profile on TRAIN/VALIDATION; retain hard-stop and spread guards.",
            "Stratify TRAIN/VALIDATION diagnostics by session, trend alignment, and volatility regime; pre-register any thresholds before future untouched OOS.",
            "Keep Brain-v3 immutable and do not use these OOS outcomes to fit, select, or tune Brain-v4."],
        "safety": {"orders": 0, "runtime_paper_trades": 0, "live_permission": False}}
    Path("reports/brain_v3_diagnostic.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"shadow": result["shadow_original_37"] | {"decisions": "see report"},
                      "oos_rows": len(oos), "report": "reports/brain_v3_diagnostic.json"}, indent=2))


if __name__ == "__main__": main()
