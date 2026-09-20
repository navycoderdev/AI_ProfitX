from typing import Protocol


class FeatureEngine(Protocol):
    def build(self, candles: list[dict], context: dict) -> dict: ...
