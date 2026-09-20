from typing import Protocol


class ModelValidator(Protocol):
    def validate(self, candidate_version: str) -> dict: ...
