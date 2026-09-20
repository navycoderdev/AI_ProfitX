from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ModelStatus(StrEnum):
    TRAINING = "TRAINING"
    CANDIDATE = "CANDIDATE"
    VALIDATING = "VALIDATING"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    APPROVED = "APPROVED"
    PRODUCTION = "PRODUCTION"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


@dataclass(frozen=True, slots=True)
class ModelMetadata:
    model_id: str
    parent_model: str | None
    training_dataset: dict[str, Any]
    training_period: dict[str, str]
    feature_version: str
    algorithm: str
    hyperparameters: dict[str, Any]
    training_metrics: dict[str, Any] = field(default_factory=dict)
    validation_metrics: dict[str, Any] = field(default_factory=dict)
    out_of_sample_metrics: dict[str, Any] = field(default_factory=dict)
    regime_metrics: dict[str, Any] = field(default_factory=dict)
    transaction_cost_assumptions: dict[str, Any] = field(default_factory=dict)
    creation_timestamp: datetime | None = None
    deployment_status: ModelStatus = ModelStatus.TRAINING


@dataclass(frozen=True, slots=True)
class RetrainingPolicy:
    minimum_new_observations: int = 100
    schedule_interval_seconds: int | None = None
    permit_performance_drift_investigation: bool = True
    permit_data_drift_investigation: bool = True


@dataclass(frozen=True, slots=True)
class RetrainingDecision:
    should_train: bool
    reasons: tuple[str, ...]
