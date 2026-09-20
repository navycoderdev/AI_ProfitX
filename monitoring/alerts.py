from datetime import datetime, timezone
from uuid import uuid4

from database.models import OperationalAlert
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


class AlertService:
    def __init__(self, sessions: sessionmaker[Session]) -> None: self.sessions = sessions
    def raise_alert(self, severity: str, code: str, message: str, details: dict | None = None) -> str:
        identifier = str(uuid4())
        with self.sessions() as session:
            session.add(OperationalAlert(alert_id=identifier, timestamp=datetime.now(timezone.utc), severity=severity,
                code=code, message=message, details=details or {})); session.commit()
        return identifier
    def acknowledge(self, alert_id: str, operator: str) -> None:
        with self.sessions() as session:
            item = session.scalar(select(OperationalAlert).where(OperationalAlert.alert_id == alert_id))
            if not item: raise KeyError(f"Unknown alert: {alert_id}")
            item.acknowledged_at, item.acknowledged_by = datetime.now(timezone.utc), operator; session.commit()
    def recent(self, limit: int = 100) -> list[dict]:
        with self.sessions() as session:
            rows = session.scalars(select(OperationalAlert).order_by(OperationalAlert.id.desc()).limit(limit)).all()
            return [{"alert_id": item.alert_id, "timestamp": item.timestamp, "severity": item.severity, "code": item.code,
                     "message": item.message, "details": item.details, "acknowledged_at": item.acknowledged_at,
                     "acknowledged_by": item.acknowledged_by} for item in rows]
    def evaluate(self, *, mt5_connected: bool, database_ok: bool, last_tick_at: datetime | None, stale_seconds: int,
                 spread_points: float | None = None, maximum_spread: float | None = None, errors: tuple[str, ...] = ()) -> list[str]:
        alerts = []
        if not mt5_connected: alerts.append(self.raise_alert("CRITICAL", "MT5_DISCONNECTED", "MT5 connection is unavailable."))
        if not database_ok: alerts.append(self.raise_alert("CRITICAL", "DATABASE_FAILURE", "Database health check failed."))
        if last_tick_at is not None and last_tick_at.tzinfo is None: last_tick_at = last_tick_at.replace(tzinfo=timezone.utc)
        if last_tick_at is None or (datetime.now(timezone.utc) - last_tick_at).total_seconds() > stale_seconds:
            alerts.append(self.raise_alert("WARNING", "DATA_FEED_STALE", "Market data feed is stale."))
        if spread_points is not None and maximum_spread is not None and spread_points > maximum_spread:
            alerts.append(self.raise_alert("WARNING", "ABNORMAL_SPREAD", "Observed spread exceeds configured maximum.", {"spread": spread_points}))
        for error in errors: alerts.append(self.raise_alert("ERROR", error, f"Operational error: {error}"))
        return alerts
