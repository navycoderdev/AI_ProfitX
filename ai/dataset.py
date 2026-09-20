from datetime import datetime

from ai.types import ChronologicalDataset, LabeledObservation, ProposalAction


class DatasetBuilder:
    """Builds explicitly time-ordered samples; labels must occur strictly after decision-time features."""
    def __init__(self, feature_names: tuple[str, ...]) -> None:
        if not feature_names: raise ValueError("At least one approved feature is required.")
        self.feature_names = feature_names

    def build(self, rows: list[dict]) -> list[LabeledObservation]:
        observations = []
        for row in rows:
            timestamp, cutoff, target_at = row["timestamp"], row["raw_data_cutoff"], row["target_timestamp"]
            if cutoff > timestamp: raise ValueError("Feature snapshot raw cutoff is after decision timestamp.")
            if target_at <= timestamp: raise ValueError("Target must be observed strictly after decision timestamp.")
            values = row["features"]
            if any(name not in values or values[name] is None for name in self.feature_names): continue
            target = ProposalAction(row["target"])
            observations.append(LabeledObservation(timestamp, cutoff, row["feature_snapshot_id"], row["feature_version"],
                {name: float(values[name]) for name in self.feature_names}, target, target_at))
        return sorted(observations, key=lambda item: item.timestamp)

    def split(self, observations: list[LabeledObservation], train_fraction: float = .60, validation_fraction: float = .20) -> ChronologicalDataset:
        if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1 or train_fraction + validation_fraction >= 1:
            raise ValueError("Invalid chronological split fractions.")
        if len(observations) < 5: raise ValueError("At least five chronological observations are required.")
        ordered = sorted(observations, key=lambda item: item.timestamp)
        train_end, validation_end = int(len(ordered) * train_fraction), int(len(ordered) * (train_fraction + validation_fraction))
        train, validation, out = tuple(ordered[:train_end]), tuple(ordered[train_end:validation_end]), tuple(ordered[validation_end:])
        if not train or not validation or not out: raise ValueError("Chronological split produced an empty partition.")
        if train[-1].timestamp >= validation[0].timestamp or validation[-1].timestamp >= out[0].timestamp:
            raise ValueError("Chronological partitions overlap.")
        return ChronologicalDataset(self.feature_names, train, validation, out)
