from typing import Protocol


class RegimeDetector(Protocol):
    def classify(self, features: dict) -> tuple[str, float]: ...
