from datetime import datetime, timezone
from typing import Callable

from config.settings import Settings
from core.exceptions import ModeViolationError
from database.audit import AuditRepository
from database.models import SystemControlState
from risk.guards import EmergencyKillSwitch
from sqlalchemy.orm import Session, sessionmaker


class EmergencyStopService:
    """Persistent global order block. Default policy preserves existing positions unchanged."""
    KEY = "emergency_stop"
    def __init__(self, sessions: sessionmaker[Session], settings: Settings, audit: AuditRepository, kill_switch: EmergencyKillSwitch | None = None) -> None:
        self.sessions, self.settings, self.audit, self.kill_switch = sessions, settings, audit, kill_switch
    def status(self) -> dict:
        with self.sessions() as session:
            row = session.get(SystemControlState, self.KEY)
            return row.value if row else {"active": False, "position_policy": self.settings.emergency_position_policy}
    def activate(self, operator: str, reason: str) -> dict:
        if not operator or not reason: raise ValueError("Operator and reason are required for emergency stop.")
        state = {"active": True, "operator": operator, "reason": reason, "position_policy": self.settings.emergency_position_policy,
                 "activated_at": datetime.now(timezone.utc).isoformat()}
        with self.sessions() as session:
            row = session.get(SystemControlState, self.KEY)
            if row: row.value, row.updated_at = state, datetime.now(timezone.utc)
            else: session.add(SystemControlState(key=self.KEY, value=state))
            session.commit()
        if self.kill_switch: self.kill_switch.trigger(f"EMERGENCY_STOP:{reason}")
        self.audit.write("operator.emergency_stop", self.settings.app_env.value, state)
        return state
    def reactivate(self, operator: str, recovery_procedure: str) -> dict:
        if operator not in self.settings.control_recovery_operators: raise PermissionError("Operator is not configured for emergency reactivation.")
        if recovery_procedure != "controlled_operator_reactivation": raise ValueError("Configured recovery procedure was not satisfied.")
        state = {"active": False, "operator": operator, "recovery_procedure": recovery_procedure,
                 "reactivated_at": datetime.now(timezone.utc).isoformat(), "position_policy": self.settings.emergency_position_policy}
        with self.sessions() as session:
            row = session.get(SystemControlState, self.KEY)
            if row: row.value, row.updated_at = state, datetime.now(timezone.utc)
            else: session.add(SystemControlState(key=self.KEY, value=state))
            session.commit()
        if self.kill_switch: self.kill_switch.recover(recovery_procedure, operator)
        self.audit.write("operator.emergency_reactivated", self.settings.app_env.value, state)
        return state
    def assert_orders_allowed(self) -> None:
        state = self.status()
        if state.get("active"): raise ModeViolationError(f"New order blocked by emergency stop: {state.get('reason', 'unspecified')}")
    def recover_on_startup(self) -> dict:
        state = self.status()
        if state.get("active") and self.kill_switch: self.kill_switch.trigger(f"RESTORED_EMERGENCY_STOP:{state.get('reason', 'unspecified')}")
        return state
