from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from database.models import DecisionMemory, MarketSnapshot, TradeMemory, TradeMemoryEvent
from memory.types import AIDecisionMemory, PreTradeSnapshot


class MarketSnapshotRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None: self.sessions = sessions
    def save(self, snapshot: PreTradeSnapshot) -> str:
        identifier = str(uuid4())
        with self.sessions() as session:
            session.add(MarketSnapshot(snapshot_id=identifier, timestamp=snapshot.timestamp, symbol=snapshot.symbol.upper(),
                timeframe=snapshot.timeframe.upper(), regime=snapshot.regime, spread=snapshot.spread, volatility=snapshot.volatility,
                session=snapshot.session, market_data=snapshot.market_data, account_state=snapshot.account_state,
                existing_exposure=snapshot.existing_exposure)); session.commit()
        return identifier
    def get(self, snapshot_id: str) -> dict:
        with self.sessions() as session:
            item = session.scalar(select(MarketSnapshot).where(MarketSnapshot.snapshot_id == snapshot_id))
            if not item: raise KeyError(f"Unknown market snapshot: {snapshot_id}")
            return {"snapshot_id": item.snapshot_id, "timestamp": item.timestamp, "symbol": item.symbol, "timeframe": item.timeframe,
                    "regime": item.regime, "spread": item.spread, "volatility": item.volatility, "session": item.session,
                    "market_data": item.market_data, "account_state": item.account_state, "existing_exposure": item.existing_exposure}


class DecisionMemoryRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None: self.sessions = sessions
    def save(self, *, timestamp: datetime, symbol: str, timeframe: str, environment: str, strategy_version: str,
             model_version: str, feature_version: str, market_snapshot_id: str, decision: AIDecisionMemory,
             feature_snapshot_id: str | None = None, trade_id: str | None = None, risk_metadata: dict | None = None) -> str:
        identifier = str(uuid4())
        with self.sessions() as session:
            session.add(DecisionMemory(decision_id=identifier, trade_id=trade_id, timestamp=timestamp, symbol=symbol.upper(),
                timeframe=timeframe.upper(), environment=environment, strategy_version=strategy_version, model_version=model_version,
                feature_version=feature_version, feature_snapshot_id=feature_snapshot_id, market_snapshot_id=market_snapshot_id,
                direction=decision.direction, confidence=decision.confidence, proposed_entry=decision.proposed_entry,
                proposed_stop=decision.proposed_stop, proposed_target=decision.proposed_target, decision_metadata=decision.metadata,
                risk_metadata=risk_metadata or {})); session.commit()
        return identifier
    def attach_risk(self, decision_id: str, risk_metadata: dict) -> None:
        with self.sessions() as session:
            item = session.scalar(select(DecisionMemory).where(DecisionMemory.decision_id == decision_id))
            if not item: raise KeyError(f"Unknown decision: {decision_id}")
            item.risk_metadata = risk_metadata; session.commit()
    def attach_trade(self, decision_id: str, trade_id: str) -> None:
        with self.sessions() as session:
            item = session.scalar(select(DecisionMemory).where(DecisionMemory.decision_id == decision_id))
            if not item: raise KeyError(f"Unknown decision: {decision_id}")
            item.trade_id = trade_id; session.commit()
    def get(self, decision_id: str) -> dict:
        with self.sessions() as session:
            item = session.scalar(select(DecisionMemory).where(DecisionMemory.decision_id == decision_id))
            if not item: raise KeyError(f"Unknown decision: {decision_id}")
            return {"decision_id": item.decision_id, "trade_id": item.trade_id, "timestamp": item.timestamp, "symbol": item.symbol,
                    "timeframe": item.timeframe, "environment": item.environment, "strategy_version": item.strategy_version,
                    "model_version": item.model_version, "feature_version": item.feature_version, "feature_snapshot_id": item.feature_snapshot_id,
                    "market_snapshot_id": item.market_snapshot_id, "direction": item.direction, "confidence": item.confidence,
                    "proposed_entry": item.proposed_entry, "proposed_stop": item.proposed_stop, "proposed_target": item.proposed_target,
                    "decision_metadata": item.decision_metadata, "risk_metadata": item.risk_metadata}


class TradeMemoryRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None: self.sessions = sessions
    def record_execution(self, trade_id: str, decision_id: str | None, execution: dict) -> None:
        with self.sessions() as session:
            item = session.scalar(select(TradeMemory).where(TradeMemory.trade_id == trade_id))
            if not item:
                item = TradeMemory(trade_id=trade_id, decision_id=decision_id, execution=execution); session.add(item)
            else:
                item.decision_id, item.execution, item.updated_at = decision_id or item.decision_id, execution, datetime.now(timezone.utc)
            session.add(TradeMemoryEvent(trade_id=trade_id, event_type="execution", payload=execution)); session.commit()
    def record_outcome(self, trade_id: str, outcome: dict, label: dict) -> None:
        with self.sessions() as session:
            item = session.scalar(select(TradeMemory).where(TradeMemory.trade_id == trade_id))
            if not item: item = TradeMemory(trade_id=trade_id); session.add(item)
            item.outcome, item.label, item.updated_at = outcome, label, datetime.now(timezone.utc)
            session.add(TradeMemoryEvent(trade_id=trade_id, event_type="outcome", payload={"outcome": outcome, "label": label})); session.commit()
    def append_event(self, trade_id: str, event_type: str, payload: dict) -> None:
        with self.sessions() as session:
            session.add(TradeMemoryEvent(trade_id=trade_id, event_type=event_type, payload=payload)); session.commit()
    def get(self, trade_id: str) -> dict:
        with self.sessions() as session:
            item = session.scalar(select(TradeMemory).where(TradeMemory.trade_id == trade_id))
            if not item: raise KeyError(f"Unknown trade memory: {trade_id}")
            events = session.scalars(select(TradeMemoryEvent).where(TradeMemoryEvent.trade_id == trade_id).order_by(TradeMemoryEvent.id)).all()
            return {"trade_id": item.trade_id, "decision_id": item.decision_id, "execution": item.execution,
                    "outcome": item.outcome, "label": item.label,
                    "events": [{"type": event.event_type, "timestamp": event.timestamp, "payload": event.payload} for event in events]}
