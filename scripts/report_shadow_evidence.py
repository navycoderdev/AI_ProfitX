"""Verify real forward Shadow evidence after the bounded observer exits."""
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from ai.shadow import ARTIFACT_SHA256, DATASET_HASH, MODEL_VERSION, SYMBOLS
from config.settings import get_settings
from database.models import (AuditLog, DecisionMemory, FeatureSnapshot, ModelVersion,
    ShadowDecision, ShadowOutcome, TradeMemory)
from database.session import initialize_database


def main():
    runtime = json.loads(Path("reports/shadow_runtime.json").read_text(encoding="utf-8"))
    if runtime.get("trading_mode") != "SHADOW" or runtime.get("live_permission") is not False:
        raise RuntimeError("Runtime evidence was not produced in safe SHADOW mode.")
    start = datetime.fromisoformat(runtime["runtime_start"])
    end = datetime.fromisoformat(runtime["runtime_end"])
    sessions = initialize_database(get_settings())
    with sessions() as session:
        rows = session.execute(select(ShadowDecision, FeatureSnapshot).join(FeatureSnapshot,
            FeatureSnapshot.snapshot_id == ShadowDecision.feature_snapshot_id).where(
            ShadowDecision.model_version == MODEL_VERSION)).all()
        outcomes = session.scalars(select(ShadowOutcome)).all()
        paper = session.scalar(select(func.count(TradeMemory.id)).join(DecisionMemory,
            TradeMemory.decision_id == DecisionMemory.decision_id).where(DecisionMemory.environment == "PAPER")) or 0
        orders = session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "mt5.order.submit",
            AuditLog.created_at >= start.replace(tzinfo=None))) or 0
        model_status = session.scalar(select(ModelVersion.stage).where(ModelVersion.version == MODEL_VERSION))
    decisions = []
    for row, snapshot in rows:
        at = row.decision_at.replace(tzinfo=timezone.utc) if row.decision_at.tzinfo is None else row.decision_at
        cutoff = snapshot.raw_data_cutoff.replace(tzinfo=timezone.utc) if snapshot.raw_data_cutoff.tzinfo is None else snapshot.raw_data_cutoff
        if start < at <= end:
            if (row.artifact_hash != ARTIFACT_SHA256 or row.dataset_hash != DATASET_HASH or
                row.environment != "SHADOW" or row.order_submitted or row.feature_set_version != "feature-set-v1" or
                len(snapshot.values) < 22 or cutoff >= at):
                raise RuntimeError("Shadow decision lineage or closed-candle gate failed.")
            decisions.append(row)
    per_symbol = {symbol: Counter(row.final_decision for row in decisions if row.symbol == symbol) for symbol in SYMBOLS}
    if any(not sum(per_symbol[symbol].values()) for symbol in SYMBOLS):
        raise RuntimeError("At least one current symbol lacks a genuine forward decision.")
    if orders or paper or model_status != "CANDIDATE" or get_settings().allow_live_trading:
        raise RuntimeError("Shadow execution or governance safety gate failed.")
    resolved = {outcome.decision_id for outcome in outcomes}
    risk = Counter(row.risk_status for row in decisions)
    reasons = Counter(code for row in decisions for code in row.risk_reason_codes)
    result = {"runtime_start": start.isoformat(), "runtime_end": end.isoformat(),
        "symbols_observed": list(SYMBOLS), "model_version": MODEL_VERSION,
        "artifact_hash": ARTIFACT_SHA256, "model_status": model_status,
        "total_decisions": len(decisions),
        "decision_counts": dict(Counter(row.final_decision for row in decisions)),
        "per_symbol": {symbol: dict(per_symbol[symbol]) for symbol in SYMBOLS},
        "risk": {"PASS": risk["PASS"], "BLOCK": risk["BLOCK"], "reason_codes": dict(reasons)},
        "outcomes": {"pending": sum(row.decision_id not in resolved for row in decisions),
            "resolved": sum(row.decision_id in resolved for row in decisions)},
        "mt5_order_submit_audit_events": orders, "runtime_paper_trades": paper,
        "live_permission": False, "runtime_report": "reports/shadow_runtime.json"}
    Path("reports/shadow_evidence.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
