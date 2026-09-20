from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True, slots=True)
class CostAssumptions:
    point_size: float = 0.00001
    commission_per_unit: float = 0.0
    slippage_points: float = 0.0
    partial_fill_ratio: float = 1.0
    leverage: float = 30.0

    def __post_init__(self) -> None:
        if not 0 < self.partial_fill_ratio <= 1: raise ValueError("partial_fill_ratio must be in (0, 1].")


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    symbol: str
    timeframe: str
    starting_cash: float = 10_000.0
    position_size: float = 1_000.0
    max_positions: int = 1
    strategy_version: str = "baseline-v1"
    data_version: str = "raw-market-v1"
    feature_version: str = "phase3-v1"
    costs: CostAssumptions = field(default_factory=CostAssumptions)


@dataclass(frozen=True, slots=True)
class OrderIntent:
    side: Side
    quantity: float
    submitted_at: datetime
    stop_loss: float | None = None
    take_profit: float | None = None
    reason: str = "strategy"


@dataclass(slots=True)
class SimulatedPosition:
    position_id: int
    side: Side
    quantity: float
    entry_price: float
    opened_at: datetime
    opened_index: int
    stop_loss: float | None
    take_profit: float | None
    entry_cost: float
    initial_margin: float
    mfe: float = 0.0
    mae: float = 0.0


@dataclass(frozen=True, slots=True)
class SimulatedTrade:
    position_id: int
    side: Side
    quantity: float
    entry_price: float
    exit_price: float
    opened_at: datetime
    closed_at: datetime
    gross_pnl: float
    net_pnl: float
    transaction_costs: float
    slippage_impact: float
    exit_reason: str
    holding_seconds: float
    mfe: float
    mae: float


@dataclass(frozen=True, slots=True)
class BacktestReport:
    metadata: dict[str, Any]
    metrics: dict[str, float | int | None]
    equity_curve: tuple[dict[str, Any], ...]
    trades: tuple[SimulatedTrade, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"metadata": self.metadata, "metrics": self.metrics, "equity_curve": list(self.equity_curve),
                "trades": [asdict(trade) for trade in self.trades]}
