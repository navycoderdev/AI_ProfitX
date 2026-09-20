from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ProposalAction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True, slots=True)
class TradeProposal:
    action: ProposalAction
    confidence: float
    model_version: str
    feature_version: str
    decision_timestamp: datetime
    entry_preference: float | None = None
    stop_proposal: float | None = None
    target_proposal: float | None = None
    holding_horizon: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LabeledObservation:
    timestamp: datetime
    raw_data_cutoff: datetime
    feature_snapshot_id: str
    feature_version: str
    features: dict[str, float]
    target: ProposalAction
    target_timestamp: datetime


@dataclass(frozen=True, slots=True)
class ChronologicalDataset:
    feature_names: tuple[str, ...]
    train: tuple[LabeledObservation, ...]
    validation: tuple[LabeledObservation, ...]
    out_of_sample: tuple[LabeledObservation, ...]
