from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from mt5.types import MarketOrder


class TradeState(StrEnum):
    SIGNAL_CREATED = "SIGNAL_CREATED"
    RISK_CHECK = "RISK_CHECK"
    APPROVED = "APPROVED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_ACKNOWLEDGED = "ORDER_ACKNOWLEDGED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    POSITION_OPEN = "POSITION_OPEN"
    POSITION_MANAGED = "POSITION_MANAGED"
    EXIT_REQUESTED = "EXIT_REQUESTED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class PositionOwner:
    strategy_id: str
    model_id: str
    trade_id: str
    magic_number: int


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    break_even_after_points: float | None = None
    trailing_stop_points: float | None = None
    partial_exit_fraction: float | None = None
    max_holding_seconds: int | None = None


@dataclass(slots=True)
class TradeRecord:
    order: MarketOrder
    owner: PositionOwner
    state: TradeState = TradeState.SIGNAL_CREATED
    trade_id: str = field(default_factory=lambda: str(uuid4()))
    mt5_ticket: int | None = None
    position_id: int | None = None
    filled_volume: float = 0.0
    telemetry: list[dict[str, Any]] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_telemetry(self, event: str, payload: dict[str, Any]) -> None:
        self.telemetry.append({"event": event, "timestamp": datetime.now(timezone.utc).isoformat(), "payload": payload})
