from strategies.interfaces import SignalProposal


class AIDecisionModel:
    """Inference contract; model training and deployment stay separate."""
    version: str

    def predict(self, features: dict, regime: str) -> SignalProposal:
        raise NotImplementedError
