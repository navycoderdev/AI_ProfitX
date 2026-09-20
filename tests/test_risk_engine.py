from datetime import datetime, timedelta, timezone

from core.context import TradingContext
from core.events import EventBus
from core.modes import RuntimeEnvironment, TradingMode
from config.settings import Settings
from database.audit import AuditRepository
from database.models import AuditLog
from database.session import initialize_database
from risk.engine import DeterministicRiskEngine
from risk.types import (AccountRiskState, OpenExposure, RiskProfile, RiskReasonCode, RiskRequest, RiskStatus)


def context() -> TradingContext:
    return TradingContext("acct-1", "EURUSD", "M5", "RANGE", "candidate", "baseline", "conservative",
                          RuntimeEnvironment.TEST, TradingMode.PAPER)


def request(**overrides) -> RiskRequest:
    values = {"symbol": "EURUSD", "direction": "LONG", "entry_price": 1.1000, "stop_loss": 1.0980,
              "take_profit": 1.1040, "requested_quantity": 1000.0, "point_size": .00001,
              "spread_points": 10.0, "expected_slippage_points": 2.0, "correlation_group": "USD", "session": "LONDON"}
    values.update(overrides)
    return RiskRequest(**values)


def state(**overrides) -> AccountRiskState:
    values = {"balance": 10_000.0, "equity": 10_000.0, "high_water_equity": 10_000.0, "free_margin": 9_000.0}
    values.update(overrides)
    return AccountRiskState(**values)


def test_approved_trade_is_risk_sized_and_reduced_by_requested_limit():
    engine = DeterministicRiskEngine(RiskProfile(maximum_lot=500, allowed_modes=(TradingMode.PAPER,)))
    decision = engine.evaluate(context(), request(requested_quantity=1_000), state())
    assert decision.status is RiskStatus.REDUCED_SIZE
    assert decision.position_size == 500
    assert decision.reason_codes == (RiskReasonCode.SIZE_REDUCED,)


def test_invalid_stop_spread_and_risk_reward_are_auditable_reason_codes():
    engine = DeterministicRiskEngine(RiskProfile(maximum_spread_points=5, minimum_risk_reward=2, allowed_modes=(TradingMode.PAPER,)))
    decision = engine.evaluate(context(), request(stop_loss=1.09995, take_profit=1.10002, spread_points=8), state())
    assert decision.status is RiskStatus.REJECTED
    assert {RiskReasonCode.MIN_STOP_DISTANCE, RiskReasonCode.MAX_SPREAD, RiskReasonCode.MIN_RISK_REWARD}.issubset(decision.reason_codes)


def test_drawdown_creates_incident_and_blocks_future_positions():
    incidents = []; bus = EventBus(); bus.subscribe("risk.incident", incidents.append)
    engine = DeterministicRiskEngine(RiskProfile(maximum_drawdown_fraction=.1, allowed_modes=(TradingMode.PAPER,)), events=bus)
    decision = engine.evaluate(context(), request(), state(equity=8_900))
    assert RiskReasonCode.MAX_DRAWDOWN in decision.reason_codes
    assert engine.kill_switch.active is True
    assert len(incidents) == 1
    follow_up = engine.evaluate(context(), request(), state())
    assert RiskReasonCode.KILL_SWITCH_ACTIVE in follow_up.reason_codes


def test_daily_weekly_and_consecutive_loss_guards_trigger_circuit_breaker():
    profile = RiskProfile(maximum_daily_loss_fraction=.02, maximum_weekly_loss_fraction=.04, maximum_consecutive_losses=2,
                          allowed_modes=(TradingMode.PAPER,))
    decision = DeterministicRiskEngine(profile).evaluate(context(), request(), state(daily_realized_pnl=-300, weekly_realized_pnl=-500, consecutive_losses=2))
    assert {RiskReasonCode.MAX_DAILY_LOSS, RiskReasonCode.MAX_WEEKLY_LOSS, RiskReasonCode.MAX_CONSECUTIVE_LOSSES}.issubset(decision.reason_codes)


def test_exposure_and_frequency_limits_block_order():
    profile = RiskProfile(maximum_symbol_exposure=500, maximum_correlated_exposure=900, maximum_trades_per_day=1,
                          maximum_trades_per_session=1, allowed_modes=(TradingMode.PAPER,))
    open_position = OpenExposure("EURUSD", 500, 1.1, "USD")
    decision = DeterministicRiskEngine(profile).evaluate(context(), request(), state(trades_today=1, trades_session=1, open_exposures=(open_position,)))
    assert RiskReasonCode.MAX_TRADE_FREQUENCY in decision.reason_codes
    assert RiskReasonCode.MAX_SYMBOL_EXPOSURE in decision.reason_codes


def test_cooldown_and_minimum_margin_block_order():
    profile = RiskProfile(cooldown_seconds=300, minimum_free_margin=500, allowed_modes=(TradingMode.PAPER,))
    decision = DeterministicRiskEngine(profile).evaluate(context(), request(), state(free_margin=100, last_trade_closed_at=datetime.now(timezone.utc) - timedelta(seconds=20)))
    assert {RiskReasonCode.COOLDOWN_ACTIVE, RiskReasonCode.MIN_FREE_MARGIN}.issubset(decision.reason_codes)


def test_kill_switch_requires_configured_recovery_not_ai_signal():
    profile = RiskProfile(recovery_procedure="risk-officer-ticket", allowed_modes=(TradingMode.PAPER,))
    engine = DeterministicRiskEngine(profile); engine.kill_switch.trigger("operator test")
    assert engine.evaluate(context(), request(), state()).approved is False
    try:
        engine.recover_kill_switch("wrong", "risk-officer")
        assert False, "incorrect procedure must fail"
    except ValueError:
        pass
    engine.recover_kill_switch("risk-officer-ticket", "risk-officer")
    assert engine.kill_switch.active is False


def test_rejection_is_persisted_to_audit_log(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/risk.db")
    sessions = initialize_database(settings)
    decision = DeterministicRiskEngine(RiskProfile(maximum_spread_points=1, allowed_modes=(TradingMode.PAPER,)), AuditRepository(sessions)).evaluate(
        context(), request(spread_points=10), state())
    with sessions() as session:
        record = session.query(AuditLog).filter_by(action="risk.rejected").one()
    assert decision.status is RiskStatus.REJECTED
    assert "MAX_SPREAD" in record.payload["reason_codes"]
