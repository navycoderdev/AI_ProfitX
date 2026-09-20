from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class MarketOrder:
    symbol: str
    side: OrderSide
    volume: float
    stop_loss: float | None = None
    take_profit: float | None = None
    deviation: int = 20
    magic: int = 0
    comment: str = ""
    client_order_id: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    accepted: bool
    final: bool
    retcode: int | None
    message: str
    mt5_ticket: int | None = None
    position_id: int | None = None
    deal_id: int | None = None
    request_price: float | None = None
    fill_price: float | None = None
    requested_volume: float | None = None
    filled_volume: float | None = None
    spread_points: int | None = None
    slippage_points: float | None = None
    executed_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def audit_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["executed_at"] = self.executed_at.isoformat() if self.executed_at else None
        return payload
