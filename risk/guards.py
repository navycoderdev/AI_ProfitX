from datetime import datetime, timezone

from risk.types import AccountRiskState, RiskProfile, RiskReasonCode, RiskRequest, RiskViolation


class PositionSizer:
    def size(self, request: RiskRequest, state: AccountRiskState, profile: RiskProfile) -> float:
        if request.stop_loss is None: raise ValueError("A hard stop loss is required for risk sizing.")
        distance = abs(request.entry_price - request.stop_loss)
        if distance <= 0: raise ValueError("Stop loss must differ from entry price.")
        risk_budget = state.equity * profile.risk_per_trade_fraction
        calculated = risk_budget / distance
        return min(calculated, request.requested_quantity or calculated, profile.maximum_lot)


class ExposureManager:
    def validate(self, request: RiskRequest, quantity: float, state: AccountRiskState, profile: RiskProfile) -> list[RiskViolation]:
        requested = abs(quantity * request.entry_price)
        same_symbol = sum(item.notional for item in state.open_exposures if item.symbol == request.symbol)
        correlated = sum(item.notional for item in state.open_exposures if item.correlation_group == request.correlation_group)
        violations = []
        if len(state.open_exposures) >= profile.maximum_open_positions:
            violations.append(RiskViolation(RiskReasonCode.MAX_OPEN_POSITIONS, "Maximum open positions reached."))
        if same_symbol + requested > profile.maximum_symbol_exposure:
            violations.append(RiskViolation(RiskReasonCode.MAX_SYMBOL_EXPOSURE, "Maximum symbol exposure exceeded."))
        if correlated + requested > profile.maximum_correlated_exposure:
            violations.append(RiskViolation(RiskReasonCode.MAX_CORRELATED_EXPOSURE, "Maximum correlated exposure exceeded."))
        return violations


class DrawdownGuard:
    def validate(self, state: AccountRiskState, profile: RiskProfile) -> RiskViolation | None:
        drawdown = (state.high_water_equity - state.equity) / state.high_water_equity if state.high_water_equity else 0.0
        if drawdown >= profile.maximum_drawdown_fraction:
            return RiskViolation(RiskReasonCode.MAX_DRAWDOWN, "Maximum account drawdown reached.", True, {"drawdown": drawdown})
        return None


class DailyLossGuard:
    def validate(self, state: AccountRiskState, profile: RiskProfile) -> list[RiskViolation]:
        violations = []
        if state.daily_realized_pnl <= -state.balance * profile.maximum_daily_loss_fraction:
            violations.append(RiskViolation(RiskReasonCode.MAX_DAILY_LOSS, "Daily loss circuit breaker reached.", True))
        if state.weekly_realized_pnl <= -state.balance * profile.maximum_weekly_loss_fraction:
            violations.append(RiskViolation(RiskReasonCode.MAX_WEEKLY_LOSS, "Weekly loss circuit breaker reached.", True))
        if state.consecutive_losses >= profile.maximum_consecutive_losses:
            violations.append(RiskViolation(RiskReasonCode.MAX_CONSECUTIVE_LOSSES, "Maximum consecutive losses reached.", True))
        return violations


class TradeFrequencyGuard:
    def validate(self, request: RiskRequest, state: AccountRiskState, profile: RiskProfile, now: datetime) -> list[RiskViolation]:
        violations = []
        if state.trades_today >= profile.maximum_trades_per_day:
            violations.append(RiskViolation(RiskReasonCode.MAX_TRADE_FREQUENCY, "Daily trade limit reached."))
        if state.trades_session >= profile.maximum_trades_per_session:
            violations.append(RiskViolation(RiskReasonCode.MAX_TRADE_FREQUENCY, "Session trade limit reached."))
        if state.last_trade_closed_at and (now - state.last_trade_closed_at).total_seconds() < profile.cooldown_seconds:
            violations.append(RiskViolation(RiskReasonCode.COOLDOWN_ACTIVE, "Cooldown period remains active."))
        return violations


class SpreadGuard:
    def validate(self, request: RiskRequest, profile: RiskProfile) -> list[RiskViolation]:
        violations = []
        if request.spread_points > profile.maximum_spread_points:
            violations.append(RiskViolation(RiskReasonCode.MAX_SPREAD, "Spread exceeds hard limit."))
        if request.expected_slippage_points > profile.maximum_slippage_points:
            violations.append(RiskViolation(RiskReasonCode.MAX_SLIPPAGE, "Expected slippage exceeds hard limit."))
        return violations


class EmergencyKillSwitch:
    """Only an external operator can recover a triggered account circuit breaker."""
    def __init__(self) -> None: self._active = False; self._reason: str | None = None
    @property
    def active(self) -> bool: return self._active
    @property
    def reason(self) -> str | None: return self._reason
    def trigger(self, reason: str) -> None: self._active, self._reason = True, reason
    def recover(self, recovery_procedure: str, approved_by: str) -> None:
        if not approved_by or not recovery_procedure: raise ValueError("Configured recovery procedure and approver are required.")
        self._active, self._reason = False, None
