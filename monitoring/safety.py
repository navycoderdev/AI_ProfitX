from math import isfinite

from ai.types import ProposalAction, TradeProposal


class ProposalSafetyValidator:
    """Rejects malformed inference output before it can enter risk or execution orchestration."""
    def validate(self, proposal: TradeProposal) -> None:
        if proposal.action not in ProposalAction: raise ValueError("Invalid AI action.")
        if not isfinite(proposal.confidence) or not 0 <= proposal.confidence <= 1: raise ValueError("AI confidence must be finite and within [0, 1].")
        if not proposal.model_version or not proposal.feature_version: raise ValueError("AI proposal must include model and feature versions.")
