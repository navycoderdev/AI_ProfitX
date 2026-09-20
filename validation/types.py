from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ValidationResult:
    name: str
    passed: bool
    metrics: dict[str, Any]
    failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PromotionCriteria:
    minimum_out_of_sample_trades: int = 20
    minimum_walk_forward_pass_rate: float = .60
    maximum_drawdown: float = .20
    minimum_cost_stress_net_pnl: float = 0.0
    minimum_monte_carlo_percentile_pnl: float = 0.0
    minimum_regime_coverage: int = 2
    minimum_shadow_observations: int = 100
    allow_live_promotion: bool = False


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    approved: bool
    target_stage: str
    rejection_report: dict[str, Any]
