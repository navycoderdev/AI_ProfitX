from database.audit import AuditRepository
from database.models import TradeMemory
from models.registry import ModelRegistry
from monitoring.alerts import AlertService
from monitoring.control import EmergencyStopService
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


class StartupRecoveryService:
    """Read-only reconciliation at startup; it never sends a recovery order."""
    def __init__(self, sessions: sessionmaker[Session], registry: ModelRegistry, emergency: EmergencyStopService,
                 alerts: AlertService, audit: AuditRepository) -> None:
        self.sessions, self.registry, self.emergency, self.alerts, self.audit = sessions, registry, emergency, alerts, audit
    def recover(self, remote: object | None = None) -> dict:
        emergency = self.emergency.recover_on_startup(); production = self.registry.production()
        with self.sessions() as session: local = list(session.scalars(select(TradeMemory)))
        result = {"emergency": emergency, "active_model": production["model_id"] if production else None,
                  "local_trade_memories": len(local), "mt5_reconciled": False, "mismatches": []}
        if remote is None:
            self.alerts.raise_alert("WARNING", "MT5_RECOVERY_UNAVAILABLE", "Startup recovery ran without an MT5 read adapter.")
        else:
            try:
                positions, orders, deals = remote.positions(), remote.pending_orders(), remote.deal_history()
                result.update({"mt5_reconciled": True, "positions": len(positions), "orders": len(orders), "deals": len(deals)})
                remote_tickets = {item.get("ticket") for item in positions}
                for trade in local:
                    ticket = (trade.execution.get("tickets") or {}).get("position")
                    if ticket and ticket not in remote_tickets: result["mismatches"].append({"trade_id": trade.trade_id, "position_ticket": ticket})
                if result["mismatches"]: self.alerts.raise_alert("WARNING", "STATE_MISMATCH", "Local/MT5 position mismatch detected.", result["mismatches"]) 
            except Exception as exc:
                self.alerts.raise_alert("CRITICAL", "MT5_RECOVERY_FAILED", str(exc)); result["error"] = str(exc)
        self.audit.write("system.startup_recovery", "control-plane", result)
        return result
