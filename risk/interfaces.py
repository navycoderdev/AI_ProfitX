from typing import Protocol

from core.context import TradingContext
from risk.types import AccountRiskState, RiskDecision, RiskRequest


class RiskEngine(Protocol):
    def evaluate(self, context: TradingContext, request: RiskRequest, state: AccountRiskState) -> RiskDecision: ...
