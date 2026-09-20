from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SignalProposal:
    direction: str  # LONG, SHORT, NO_TRADE
    confidence: float
    stop_loss: float | None
    take_profit: float | None
    rationale: str


class Strategy(Protocol):
    version: str
    def generate(self, features: dict, regime: str) -> SignalProposal: ...
