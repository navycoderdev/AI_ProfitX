from collections import deque

from regimes.types import RegimeAssessment, RegimeLabel


class RegimeStabilityManager:
    """Hysteresis filter: a candidate must persist before replacing the active label."""
    def __init__(self, confirmations: int = 3) -> None:
        if confirmations < 1: raise ValueError("confirmations must be positive")
        self.confirmations, self._active, self._pending = confirmations, None, deque(maxlen=confirmations)

    def apply(self, candidate: RegimeAssessment) -> RegimeAssessment:
        if candidate.label is RegimeLabel.UNCERTAIN:
            return candidate
        if self._active is None:
            self._active = candidate.label; self._pending.clear()
            return candidate
        if candidate.label is self._active:
            self._pending.clear()
            return RegimeAssessment(candidate.label, candidate.confidence, candidate.timestamp, candidate.feature_version,
                                    candidate.regime_model_version, candidate.measurements, self._active, False)
        self._pending.append(candidate.label)
        if len(self._pending) == self.confirmations and all(label is candidate.label for label in self._pending):
            previous, self._active = self._active, candidate.label; self._pending.clear()
            return RegimeAssessment(candidate.label, candidate.confidence, candidate.timestamp, candidate.feature_version,
                                    candidate.regime_model_version, candidate.measurements, previous, True)
        # A non-confirmed switch is explicitly uncertain instead of emitting noisy labels.
        return RegimeAssessment(RegimeLabel.UNCERTAIN, candidate.confidence, candidate.timestamp, candidate.feature_version,
                                candidate.regime_model_version, {**candidate.measurements, "pending_label": candidate.label.value}, self._active, False)
