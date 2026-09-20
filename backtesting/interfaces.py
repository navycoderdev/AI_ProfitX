from typing import Protocol


class BacktestEngine(Protocol):
    def run(self, specification: dict) -> dict: ...
