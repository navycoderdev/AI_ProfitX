from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class RegimeLabel(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT = "BREAKOUT"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class RegimeAssessment:
    label: RegimeLabel
    confidence: float
    timestamp: datetime
    feature_version: str
    regime_model_version: str
    measurements: dict[str, Any] = field(default_factory=dict)
    previous_label: RegimeLabel | None = None
    transition: bool = False


@dataclass(frozen=True, slots=True)
class RegimeTransition:
    symbol: str
    timeframe: str
    timestamp: datetime
    previous: RegimeLabel
    current: RegimeLabel
    confidence: float
