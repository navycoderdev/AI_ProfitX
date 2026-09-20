from ai.dataset import DatasetBuilder
from ai.evaluator import ModelEvaluator
from ai.model import ModelTrainer, ProbabilityCalibrator


class TrainingPipeline:
    """Research training flow: chronological train → validation calibration → untouched OOS evaluation."""
    def __init__(self, builder: DatasetBuilder, trainer: ModelTrainer | None = None, evaluator: ModelEvaluator | None = None) -> None:
        self.builder, self.trainer, self.evaluator = builder, trainer or ModelTrainer(), evaluator or ModelEvaluator()
    def run(self, rows: list[dict], version: str, training_kwargs: dict | None = None) -> tuple[object, dict, object]:
        observations = self.builder.build(rows); dataset = self.builder.split(observations)
        model = self.trainer.train(dataset.train, dataset.feature_names, version, **(training_kwargs or {}))
        model = ProbabilityCalibrator().calibrate(model, dataset.validation)
        metrics = {"validation": self.evaluator.evaluate(model, dataset.validation), "out_of_sample": self.evaluator.evaluate(model, dataset.out_of_sample),
                   "split": {"train": len(dataset.train), "validation": len(dataset.validation), "out_of_sample": len(dataset.out_of_sample)}}
        return model, metrics, dataset
