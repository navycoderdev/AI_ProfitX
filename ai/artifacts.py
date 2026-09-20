import json
from pathlib import Path

from ai.model import InterpretableLinearModel


class ModelArtifactManager:
    def __init__(self, directory: str | Path = "models/artifacts") -> None:
        self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=True)
    def save(self, model: InterpretableLinearModel, metadata: dict) -> Path:
        path = self.directory / f"{model.version}.json"
        if path.exists():
            raise FileExistsError(f"Model artifact is immutable and already exists: {path.name}")
        payload = {"model": model.as_dict(), "metadata": {**metadata, "model_type": "interpretable_multinomial_logistic"}}
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return path
    def load(self, version: str) -> tuple[InterpretableLinearModel, dict]:
        payload = json.loads((self.directory / f"{version}.json").read_text(encoding="utf-8")); raw = payload["model"]
        return InterpretableLinearModel(raw["version"], tuple(raw["feature_names"]), raw["means"], raw["scales"], raw["weights"], raw["temperature"]), payload["metadata"]
