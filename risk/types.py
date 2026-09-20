from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from core.modes import TradingMode


class RiskStatus(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REDUCED_SIZE = "REDUCED_SIZE"


class RiskReasonCode(StrEnum):
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    MODE_NOT_ALLOWED = "MODE_NOT_ALLOWED"
    INVALID_STOP = "INVALID_STOP"
    MIN_STOP_DISTANCE = "MIN_STOP_DISTANCE"
    MAX_SPREAD = "MAX_SPREAD"
    MAX_SLIPPAGE = "MAX_SLIPPAGE"
    MIN_FREE_MARGIN = "MIN_FREE_MARGIN"
    MIN_RISK_REWARD = "MIN_RISK_REWARD"
    MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
    MAX_SYMBOL_EXPOSURE = "MAX_SYMBOL_EXPOSURE"
    MAX_CORRELATED_EXPOSURE = "MAX_CORRELATED_EXPOSURE"
    MAX_DAILY_LOSS = "MAX_DAILY_LOSS"
    MAX_WEEKLY_LOSS = "MAX_WEEKLY_LOSS"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    MAX_CONSECUTIVE_LOSSES = "MAX_CONSECUTIVE_LOSSES"
    MAX_TRADE_FREQUENCY = "MAX_TRADE_FREQUENCY"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    SIZE_REDUCED = "SIZE_REDUCED"
    APPROVED = "APPROVED"


@dataclass(frozen=True, slots=True)
class RiskProfile:
    name: str = "conservative"
    risk_per_trade_fraction: float = .01
    maximum_lot: float = 1.0
    maximum_open_positions: int = 3
    maximum_symbol_exposure: float = 100_000.0
    maximum_correlated_exposure: float = 150_000.0
    maximum_daily_loss_fraction: float = .03
    maximum_weekly_loss_fraction: float = .06
    maximum_drawdown_fraction: float = .10
    maximum_consecutive_losses: int = 4
    maximum_trades_per_day: int = 10
    maximum_trades_per_session: int = 4
    minimum_stop_distance_points: float = 10.0
    maximum_spread_points: float = 30.0
    maximum_slippage_points: float = 10.0
    minimum_free_margin: float = 100.0
    minimum_risk_reward: float | None = 1.0
    cooldown_seconds: int = 0
    allowed_modes: tuple[TradingMode, ...] = (TradingMode.BACKTEST, TradingMode.PAPER, TradingMode.SHADOW)
    recovery_procedure: str = "manual_risk_officer_review"


@dataclass(frozen=True, slots=True)
class RiskRequest:
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float | None
    take_profit: float | None
    requested_quantity: float | None
    point_size: float
    spread_points: float
    expected_slippage_points: float
    correlation_group: str = "DEFAULT"
    session: str = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class OpenExposure:
    symbol: str
    quantity: float
    mark_price: float
    correlation_group: str = "DEFAULT"

    @property
    def notional(self) -> float: return abs(self.quantity * self.mark_price)


@dataclass(frozen=True, slots=True)
class AccountRiskState:
    balance: float
    equity: float
    high_water_equity: float
    free_margin: float
    daily_realized_pnl: float = 0.0
    weekly_realized_pnl: float = 0.0
    consecutive_losses: int = 0
    trades_today: int = 0
    trades_session: int = 0
    last_trade_closed_at: datetime | None = None
    open_exposures: tuple[OpenExposure, ...] = ()


@dataclass(frozen=True, slots=True)
class RiskViolation:
    code: RiskReasonCode
    message: str
    critical: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    status: RiskStatus
    reason_codes: tuple[RiskReasonCode, ...]
    position_size: float | None
    violations: tuple[RiskViolation, ...] = ()

    @property
    def approved(self) -> bool: return self.status in (RiskStatus.APPROVED, RiskStatus.REDUCED_SIZE)
