from datetime import datetime, timezone

from config.settings import Settings
from core.context import TradingContext
from core.modes import RuntimeEnvironment, TradingMode
from database.session import initialize_database
from execution.types import PositionOwner, TradeRecord, TradeState
from features.snapshots import FeatureSnapshotService
from memory.builder import ExperienceBuilder
from memory.outcomes import OutcomeLabeler
from memory.repositories import DecisionMemoryRepository, MarketSnapshotRepository, TradeMemoryRepository
from memory.types import AIDecisionMemory, PostTradeOutcome, PreTradeSnapshot
from mt5.types import ExecutionResult, MarketOrder, OrderSide
from risk.types import RiskDecision, RiskReasonCode, RiskStatus


def setup_memory(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/memory.db")
    sessions = initialize_database(settings); features = FeatureSnapshotService(sessions)
    builder = ExperienceBuilder(MarketSnapshotRepository(sessions), DecisionMemoryRepository(sessions), TradeMemoryRepository(sessions), features)
    return builder, features


def context():
    return TradingContext("acct", "EURUSD", "M5", "RANGE", "model-9", "strategy-3", "conservative",
                          RuntimeEnvironment.TEST, TradingMode.PAPER)


def snapshot(now):
    return PreTradeSnapshot(now, "EURUSD", "M5", {"ohlc": [1.1, 1.2, 1.0, 1.15]}, "RANGE", 12, .004, "LONDON",
                            {"equity": 10_000}, {"EURUSD": 0})


def test_no_trade_is_stored_with_feature_snapshot_and_reconstructable(tmp_path):
    builder, features = setup_memory(tmp_path); now = datetime.now(timezone.utc)
    feature_id = features.save("EURUSD", "M5", now, "phase3-v1", now, {"rsi_14": 52}, {"session": "LONDON"})
    decision_id = builder.record_decision(context(), snapshot(now), AIDecisionMemory("NO_TRADE", .72, None, None, None, {"reason": "low_edge"}), feature_id)
    restored = builder.reconstruct(decision_id)
    assert restored["decision"]["direction"] == "NO_TRADE"
    assert restored["decision"]["model_version"] == "model-9"
    assert restored["feature_snapshot"]["values"]["rsi_14"] == 52
    assert restored["market_snapshot"]["account_state"]["equity"] == 10_000


def test_executed_trade_records_risk_execution_and_full_outcome(tmp_path):
    builder, _features = setup_memory(tmp_path); now = datetime.now(timezone.utc)
    ai = AIDecisionMemory("LONG", .8, 1.1, 1.098, 1.104, {"agent": "candidate"})
    decision_id = builder.record_decision(context(), snapshot(now), ai, None)
    risk = RiskDecision(RiskStatus.APPROVED, (RiskReasonCode.APPROVED,), 1.0)
    builder.attach_risk(decision_id, ai, risk)
    trade = TradeRecord(MarketOrder("EURUSD", OrderSide.BUY, 1, 1.098, 1.104), PositionOwner("strategy-3", "model-9", "trade-x", 55),
                        state=TradeState.POSITION_OPEN, trade_id="trade-x")
    result = ExecutionResult(True, True, 10009, "done", 10, 20, 30, 1.1, 1.1001, 1, 1, 10, .0001, now)
    builder.record_execution(decision_id, trade, result, commission=2.5)
    label = builder.record_outcome("trade-x", PostTradeOutcome(1.104, "take_profit", 300, 4, 1.5, .002, .003, -.001,
                                                                  {"regime_changed": True}))
    restored = builder.reconstruct(decision_id)
    assert restored["decision"]["risk_metadata"]["position_size"] == 1
    assert restored["trade"]["execution"]["tickets"]["deal"] == 30
    assert restored["trade"]["outcome"]["pnl_after_costs"] == 1.5
    assert label["decision_quality"] == "UNDETERMINED"
    assert "REGIME_CHANGED_AFTER_ENTRY" in label["context_flags"]


def test_outcome_labeler_does_not_equate_winning_or_losing_to_decision_quality():
    positive = OutcomeLabeler().label(PostTradeOutcome(1.2, "target", 1, 4, 3, 1, 5, -1))
    negative = OutcomeLabeler().label(PostTradeOutcome(1.0, "stop", 1, -4, -5, 1, 1, -6))
    assert positive.label.startswith("FAVORABLE") and positive.decision_quality == "UNDETERMINED"
    assert negative.label.startswith("ADVERSE") and negative.decision_quality == "UNDETERMINED"
