from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from database.models import AuditLog


class AuditRepository:
    """Append-only audit writer for all MT5 boundary actions."""
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def write(self, action: str, environment: str, payload: dict[str, Any],
              session_id: str | None = None) -> None:
        with self._sessions() as session:
            session.add(AuditLog(action=action, environment=environment,
                session_id=session_id, payload={**payload, "recorded_at": datetime.now(timezone.utc).isoformat()}))
            session.commit()
