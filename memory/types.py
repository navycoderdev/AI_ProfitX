from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class PreTradeSnapshot:
    timestamp: datetime
    symbol: str
    timeframe: str
    market_data: dict[str, Any]
    regime: str | None = None
    spread: float | None = None
    volatility: float | None = None
    session: str | None = None
    account_state: dict[str, Any] = field(default_factory=dict)
    existing_exposure: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AIDecisionMemory:
    direction: str  # LONG, SHORT, NO_TRADE
    confidence: float | None
    proposed_entry: float | None
    proposed_stop: float | None
    proposed_target: float | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PostTradeOutcome:
    exit_price: float | None
    exit_reason: str | None
    holding_seconds: float | None
    pnl: float | None
    pnl_after_costs: float | None
    risk_amount: float | None
    mfe: float | None
    mae: float | None
    market_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OutcomeLabel:
    label: str
    confidence: float
    rationale: str
    r_multiple: float | None
    decision_quality: str = "UNDETERMINED"
    context_flags: tuple[str, ...] = ()
