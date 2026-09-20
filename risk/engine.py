from dataclasses import asdict
from datetime import datetime, timezone

from core.context import TradingContext
from core.events import DomainEvent, EventBus
from database.audit import AuditRepository
from risk.guards import (DailyLossGuard, DrawdownGuard, EmergencyKillSwitch, ExposureManager, PositionSizer,
                         SpreadGuard, TradeFrequencyGuard)
from risk.types import (AccountRiskState, RiskDecision, RiskProfile, RiskReasonCode, RiskRequest, RiskStatus,
                        RiskViolation)


class DeterministicRiskEngine:
    """Hard risk boundary. It consumes signals but has no dependency on, or control by, an AI model."""
    def __init__(self, profile: RiskProfile, audit: AuditRepository | None = None, events: EventBus | None = None,
                 kill_switch: EmergencyKillSwitch | None = None) -> None:
        self.profile, self.audit, self.events = profile, audit, events
        self.kill_switch = kill_switch or EmergencyKillSwitch()
        self.sizer, self.exposure = PositionSizer(), ExposureManager()
        self.drawdown, self.loss, self.frequency, self.spread = DrawdownGuard(), DailyLossGuard(), TradeFrequencyGuard(), SpreadGuard()

    def evaluate(self, context: TradingContext, request: RiskRequest, state: AccountRiskState,
                 now: datetime | None = None) -> RiskDecision:
        now = now or datetime.now(timezone.utc)
        violations: list[RiskViolation] = []
        if self.kill_switch.active:
            violations.append(RiskViolation(RiskReasonCode.KILL_SWITCH_ACTIVE, f"Kill switch active: {self.kill_switch.reason}", True))
        if context.mode not in self.profile.allowed_modes:
            violations.append(RiskViolation(RiskReasonCode.MODE_NOT_ALLOWED, f"{context.mode.value} is not permitted by risk profile.", True))
        violations.extend(filter(None, [self.drawdown.validate(state, self.profile)]))
        violations.extend(self.loss.validate(state, self.profile))
        violations.extend(self.frequency.validate(request, state, self.profile, now))
        violations.extend(self.spread.validate(request, self.profile))
        if state.free_margin < self.profile.minimum_free_margin:
            violations.append(RiskViolation(RiskReasonCode.MIN_FREE_MARGIN, "Free margin is below hard minimum."))
        if request.stop_loss is None or abs(request.entry_price - request.stop_loss) <= 0:
            violations.append(RiskViolation(RiskReasonCode.INVALID_STOP, "A valid hard stop loss is required."))
        elif abs(request.entry_price - request.stop_loss) / request.point_size < self.profile.minimum_stop_distance_points:
            violations.append(RiskViolation(RiskReasonCode.MIN_STOP_DISTANCE, "Stop distance is below hard minimum."))
        if request.take_profit is not None and request.stop_loss is not None and self.profile.minimum_risk_reward is not None:
            risk, reward = abs(request.entry_price - request.stop_loss), abs(request.take_profit - request.entry_price)
            if risk == 0 or reward / risk < self.profile.minimum_risk_reward:
                violations.append(RiskViolation(RiskReasonCode.MIN_RISK_REWARD, "Risk/reward is below the configured minimum."))
        if any(item.code is RiskReasonCode.INVALID_STOP for item in violations):
            return self._reject(context, violations)
        size = self.sizer.size(request, state, self.profile)
        violations.extend(self.exposure.validate(request, size, state, self.profile))
        if violations:
            return self._reject(context, violations)
        reduced = request.requested_quantity is not None and size < request.requested_quantity
        status = RiskStatus.REDUCED_SIZE if reduced else RiskStatus.APPROVED
        codes = (RiskReasonCode.SIZE_REDUCED,) if reduced else (RiskReasonCode.APPROVED,)
        decision = RiskDecision(status, codes, size)
        self._audit("risk.approved", context, request, decision)
        return decision

    def _reject(self, context: TradingContext, violations: list[RiskViolation]) -> RiskDecision:
        critical = [item for item in violations if item.critical]
        if critical:
            reason = ", ".join(item.code.value for item in critical)
            self.kill_switch.trigger(reason)
            incident = {"reason_codes": [item.code.value for item in critical], "recovery_procedure": self.profile.recovery_procedure}
            if self.events: self.events.publish(DomainEvent("risk.incident", incident))
            if self.audit: self.audit.write("risk.incident", context.environment.value, incident, str(context.session_id))
        decision = RiskDecision(RiskStatus.REJECTED, tuple(item.code for item in violations), None, tuple(violations))
        self._audit("risk.rejected", context, None, decision)
        return decision

    def recover_kill_switch(self, recovery_procedure: str, approved_by: str) -> None:
        if recovery_procedure != self.profile.recovery_procedure:
            raise ValueError("Recovery procedure does not match configured risk policy.")
        self.kill_switch.recover(recovery_procedure, approved_by)

    def _audit(self, action: str, context: TradingContext, request: RiskRequest | None, decision: RiskDecision) -> None:
        if self.audit:
            self.audit.write(action, context.environment.value, {"request": asdict(request) if request else None,
                "status": decision.status.value, "reason_codes": [code.value for code in decision.reason_codes],
                "position_size": decision.position_size, "violations": [{"code": item.code.value, "message": item.message} for item in decision.violations]},
                str(context.session_id))
