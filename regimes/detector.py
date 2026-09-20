from datetime import datetime, timezone

from regimes.types import RegimeAssessment, RegimeLabel


class DeterministicRegimeDetector:
    """Feature-only baseline. It uses no forward returns and declines to classify weak evidence."""
    version = "deterministic-regime-v1"

    def __init__(self, minimum_confidence: float = .60) -> None: self.minimum_confidence = minimum_confidence

    def detect(self, features: dict, timestamp: datetime) -> RegimeAssessment:
        required = ("volatility_percentile", "trend_structure", "adx_proxy_14", "break_structure_up", "break_structure_down")
        if any(features.get(key) is None for key in required):
            return self._uncertain(timestamp, features, "missing_required_measurements")
        volatility = float(features["volatility_percentile"]); adx = float(features["adx_proxy_14"] or 0) / 100
        trend = features["trend_structure"]; breakout = bool(features["break_structure_up"] or features["break_structure_down"])
        spread = features.get("spread_percentile")
        if spread is not None and float(spread) > .95:
            return self._uncertain(timestamp, features, "illiquid_spread_condition")
        if breakout:
            return self._assessment(RegimeLabel.BREAKOUT, min(.95, .60 + adx / 3 + volatility / 5), timestamp, features)
        if trend == "UP" and adx >= .25:
            return self._assessment(RegimeLabel.TRENDING_UP, min(.95, .50 + adx), timestamp, features)
        if trend == "DOWN" and adx >= .25:
            return self._assessment(RegimeLabel.TRENDING_DOWN, min(.95, .50 + adx), timestamp, features)
        if volatility >= .80:
            return self._assessment(RegimeLabel.HIGH_VOLATILITY, min(.90, .50 + volatility / 2), timestamp, features)
        if volatility <= .20:
            return self._assessment(RegimeLabel.LOW_VOLATILITY, min(.90, .50 + (1 - volatility) / 3), timestamp, features)
        if trend == "RANGE" and adx < .25:
            return self._assessment(RegimeLabel.RANGING, min(.85, .60 + (1 - adx) / 4), timestamp, features)
        return self._uncertain(timestamp, features, "ambiguous_measurements")

    def _assessment(self, label: RegimeLabel, confidence: float, timestamp: datetime, features: dict) -> RegimeAssessment:
        if confidence < self.minimum_confidence: return self._uncertain(timestamp, features, "below_confidence_threshold")
        measurements = {key: features.get(key) for key in ("trend_structure", "adx_proxy_14", "volatility_percentile", "spread_percentile", "break_structure_up", "break_structure_down", "session")}
        return RegimeAssessment(label, confidence, timestamp.astimezone(timezone.utc), str(features.get("feature_version", "UNVERSIONED")), self.version, measurements)

    def _uncertain(self, timestamp: datetime, features: dict, reason: str) -> RegimeAssessment:
        return RegimeAssessment(RegimeLabel.UNCERTAIN, 0.0, timestamp.astimezone(timezone.utc), str(features.get("feature_version", "UNVERSIONED")), self.version,
                                {"reason": reason})
