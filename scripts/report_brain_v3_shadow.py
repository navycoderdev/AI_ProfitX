"""Persist auditable Brain-v3 BTCUSD forward evidence from the original boundary."""

import json
from collections import Counter
from datetime import timezone
from pathlib import Path

from config.settings import get_settings
from database.models import ModelVersion, ShadowDecision, ShadowOutcome, SystemControlState, TradeMemory, DecisionMemory
from database.session import initialize_database
from sqlalchemy import func, select


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading:
        raise RuntimeError("Evidence requires LIVE disabled.")
    sessions = initialize_database(settings)
    with sessions() as session:
        state = session.get(SystemControlState, "brain_v3_btcusd_shadow_service")
        model = session.scalar(select(ModelVersion).where(ModelVersion.version == "Brain-v3"))
        decisions = list(session.scalars(select(ShadowDecision).where(
            ShadowDecision.model_version == "Brain-v3", ShadowDecision.symbol == "BTCUSD").order_by(
            ShadowDecision.decision_at)))
        resolved = session.scalar(select(func.count(ShadowOutcome.id)).join(ShadowDecision,
            ShadowDecision.decision_id == ShadowOutcome.decision_id).where(
            ShadowDecision.model_version == "Brain-v3", ShadowDecision.symbol == "BTCUSD")) or 0
        paper = session.scalar(select(func.count(TradeMemory.id)).join(DecisionMemory,
            DecisionMemory.decision_id == TradeMemory.decision_id).where(DecisionMemory.environment == "PAPER")) or 0
    if not state or not decisions or model.stage != "CANDIDATE":
        raise RuntimeError("Genuine Brain-v3 Shadow evidence is incomplete.")
    boundary = __import__("datetime").datetime.fromisoformat(state.value["started_at"])
    if any((row.decision_at.replace(tzinfo=timezone.utc) if row.decision_at.tzinfo is None else row.decision_at) <= boundary
           or row.environment != "SHADOW" or row.order_submitted for row in decisions):
        raise RuntimeError("Shadow boundary/environment/order invariant failed.")
    result = {"runtime_boundary": boundary.isoformat(), "evidence_generated_at": __import__("datetime").datetime.now(timezone.utc).isoformat(),
        "first_decision_at": decisions[0].decision_at.isoformat(), "last_decision_at": decisions[-1].decision_at.isoformat(),
        "source": "MT5:ICMarketsSC-Demo", "model_version": "Brain-v3", "model_status": model.stage,
        "artifact_hash": decisions[0].artifact_hash, "dataset_hash": decisions[0].dataset_hash,
        "symbol": "BTCUSD", "total_decisions": len(decisions),
        "decision_counts": dict(Counter(row.final_decision for row in decisions)),
        "risk_counts": dict(Counter(row.risk_status for row in decisions)),
        "risk_reason_codes": dict(Counter(code for row in decisions for code in row.risk_reason_codes)),
        "outcomes": {"pending": len(decisions) - resolved, "resolved": resolved},
        "orders_submitted": sum(bool(row.order_submitted) for row in decisions),
        "runtime_paper_trades": paper, "live_permission": False,
        "lineage_complete": all(row.feature_snapshot_id and row.probabilities for row in decisions)}
    Path("reports/brain_v3_btc_shadow_evidence.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
