from dataclasses import asdict, replace
from typing import Any

from core.context import TradingContext
from execution.types import TradeRecord
from features.snapshots import FeatureSnapshotService
from memory.outcomes import OutcomeLabeler
from memory.repositories import DecisionMemoryRepository, MarketSnapshotRepository, TradeMemoryRepository
from memory.types import AIDecisionMemory, PostTradeOutcome, PreTradeSnapshot
from mt5.types import ExecutionResult
from risk.types import RiskDecision


class ExperienceBuilder:
    """Creates linked, reproducible decision and trade experiences without inferring missing facts."""
    def __init__(self, markets: MarketSnapshotRepository, decisions: DecisionMemoryRepository, trades: TradeMemoryRepository,
                 feature_snapshots: FeatureSnapshotService | None = None, labeler: OutcomeLabeler | None = None) -> None:
        self.markets, self.decisions, self.trades = markets, decisions, trades
        self.feature_snapshots, self.labeler = feature_snapshots, labeler or OutcomeLabeler()

    def record_decision(self, context: TradingContext, pre_trade: PreTradeSnapshot, decision: AIDecisionMemory,
                        feature_snapshot_id: str | None, risk: RiskDecision | None = None, trade_id: str | None = None) -> str:
        if decision.direction not in {"LONG", "SHORT", "NO_TRADE"}:
            raise ValueError("Decision direction must be LONG, SHORT, or NO_TRADE.")
        # Every decision has a regime field; lack of sufficient evidence is explicitly UNCERTAIN.
        if pre_trade.regime is None:
            pre_trade = replace(pre_trade, regime="UNCERTAIN")
        market_snapshot_id = self.markets.save(pre_trade)
        risk_metadata = self._risk_payload(risk, decision) if risk else {}
        return self.decisions.save(timestamp=pre_trade.timestamp, symbol=context.symbol, timeframe=context.timeframe,
            environment=context.environment.value, strategy_version=context.strategy_version, model_version=context.model_version,
            feature_version=(self._feature_version(feature_snapshot_id) or "UNVERSIONED"), market_snapshot_id=market_snapshot_id,
            decision=decision, feature_snapshot_id=feature_snapshot_id, trade_id=trade_id, risk_metadata=risk_metadata)

    def attach_risk(self, decision_id: str, decision: AIDecisionMemory, risk: RiskDecision) -> None:
        self.decisions.attach_risk(decision_id, self._risk_payload(risk, decision))

    def record_execution(self, decision_id: str | None, trade: TradeRecord, result: ExecutionResult,
                         commission: float | None = None) -> None:
        if decision_id: self.decisions.attach_trade(decision_id, trade.trade_id)
        self.trades.record_execution(trade.trade_id, decision_id, {"request": asdict(trade.order), "fill": result.audit_payload(),
            "commission": commission, "tickets": {"order": result.mt5_ticket, "position": result.position_id, "deal": result.deal_id},
            "lifecycle": {"state": trade.state.value, "transitions": trade.transitions, "telemetry": trade.telemetry}})

    def record_lifecycle(self, trade: TradeRecord) -> None:
        self.trades.append_event(trade.trade_id, "lifecycle", {"state": trade.state.value, "transitions": trade.transitions,
            "telemetry": trade.telemetry, "owner": asdict(trade.owner)})

    def record_outcome(self, trade_id: str, outcome: PostTradeOutcome) -> dict:
        label = self.labeler.label(outcome)
        outcome_data, label_data = asdict(outcome), asdict(label)
        self.trades.record_outcome(trade_id, outcome_data, label_data)
        return label_data

    def reconstruct(self, decision_id: str) -> dict[str, Any]:
        decision = self.decisions.get(decision_id); market = self.markets.get(decision["market_snapshot_id"])
        feature = None
        if decision["feature_snapshot_id"] and self.feature_snapshots:
            feature = self.feature_snapshots.get(decision["feature_snapshot_id"])
        trade = self.trades.get(decision["trade_id"]) if decision["trade_id"] else None
        return {"decision": decision, "market_snapshot": market, "feature_snapshot": feature, "trade": trade}

    def _feature_version(self, snapshot_id: str | None) -> str | None:
        if not snapshot_id or not self.feature_snapshots: return None
        return self.feature_snapshots.get(snapshot_id)["feature_version"]

    @staticmethod
    def _risk_payload(risk: RiskDecision, decision: AIDecisionMemory) -> dict:
        risk_amount = None
        if risk.position_size is not None and decision.proposed_entry is not None and decision.proposed_stop is not None:
            risk_amount = abs(decision.proposed_entry - decision.proposed_stop) * risk.position_size
        return {"status": risk.status.value, "position_size": risk.position_size, "risk_amount": risk_amount,
                "reason_codes": [code.value for code in risk.reason_codes],
                "violations": [{"code": item.code.value, "message": item.message} for item in risk.violations]}
