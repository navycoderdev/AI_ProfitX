from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from core.modes import RuntimeEnvironment, TradingMode


@dataclass(frozen=True, slots=True)
class TradingContext:
    """Immutable context carried through every decision and audit event."""
    account_id: str | None
    symbol: str
    timeframe: str
    market_state: str
    model_version: str
    strategy_version: str
    risk_profile: str
    environment: RuntimeEnvironment
    mode: TradingMode
    session_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
