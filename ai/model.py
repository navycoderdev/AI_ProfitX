from dataclasses import dataclass, field
from math import exp, log

from ai.types import LabeledObservation, ProposalAction


CLASSES = (ProposalAction.LONG, ProposalAction.SHORT, ProposalAction.NO_TRADE)


@dataclass(slots=True)
class InterpretableLinearModel:
    version: str
    feature_names: tuple[str, ...]
    means: list[float]
    scales: list[float]
    weights: list[list[float]]
    temperature: float = 1.0

    def probabilities(self, values: dict[str, float]) -> dict[ProposalAction, float]:
        vector = [(float(values[name]) - self.means[index]) / self.scales[index] for index, name in enumerate(self.feature_names)]
        logits = [sum(weight * value for weight, value in zip(row[:-1], vector)) + row[-1] for row in self.weights]
        scaled = [value / self.temperature for value in logits]; ceiling = max(scaled); exp_values = [exp(value - ceiling) for value in scaled]
        total = sum(exp_values)
        return {label: exp_values[index] / total for index, label in enumerate(CLASSES)}

    def as_dict(self) -> dict:
        return {"version": self.version, "feature_names": list(self.feature_names), "means": self.means, "scales": self.scales,
                "weights": self.weights, "temperature": self.temperature}


class ModelTrainer:
    """Small deterministic multinomial logistic baseline; coefficients remain inspectable per feature/class."""
    def train(self, observations: tuple[LabeledObservation, ...], feature_names: tuple[str, ...], version: str,
              epochs: int = 250, learning_rate: float = .08) -> InterpretableLinearModel:
        if not observations: raise ValueError("Training partition is empty.")
        means = [sum(item.features[name] for item in observations) / len(observations) for name in feature_names]
        scales = [max((sum((item.features[name] - means[index]) ** 2 for item in observations) / len(observations)) ** .5, 1e-9) for index, name in enumerate(feature_names)]
        weights = [[0.0] * (len(feature_names) + 1) for _ in CLASSES]
        for _ in range(epochs):
            gradients = [[0.0] * (len(feature_names) + 1) for _ in CLASSES]
            for item in observations:
                vector = [(item.features[name] - means[index]) / scales[index] for index, name in enumerate(feature_names)]
                logits = [sum(weight * value for weight, value in zip(row[:-1], vector)) + row[-1] for row in weights]
                ceiling = max(logits); exps = [exp(value - ceiling) for value in logits]; total = sum(exps)
                probabilities = [value / total for value in exps]
                for class_index, label in enumerate(CLASSES):
                    error = probabilities[class_index] - float(item.target is label)
                    for index, value in enumerate(vector): gradients[class_index][index] += error * value
                    gradients[class_index][-1] += error
            for class_index in range(len(CLASSES)):
                for index in range(len(weights[class_index])): weights[class_index][index] -= learning_rate * gradients[class_index][index] / len(observations)
        return InterpretableLinearModel(version, feature_names, means, scales, weights)


class ProbabilityCalibrator:
    def calibrate(self, model: InterpretableLinearModel, validation: tuple[LabeledObservation, ...]) -> InterpretableLinearModel:
        if not validation: return model
        candidates = (.5, .75, 1.0, 1.25, 1.5, 2.0); best, best_loss = 1.0, float("inf")
        for temperature in candidates:
            model.temperature = temperature; loss = 0.0
            for item in validation: loss -= log(max(model.probabilities(item.features)[item.target], 1e-12))
            if loss < best_loss: best, best_loss = temperature, loss
        model.temperature = best
        return model
