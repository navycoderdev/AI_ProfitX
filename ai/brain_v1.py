"""Offline-only Brain-v1 research training from a frozen Dataset v1."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta

from ai.types import ProposalAction
from database.models import DatasetManifest, DatasetRow, FeatureSnapshot, RawMarketBar
from research.dataset_v1 import feature_definitions
from sqlalchemy import select


LABEL_SPEC_V1 = {"version": "label-spec-v1", "horizon_bars": 12, "timeframe": "M5", "price_reference": "M5_CLOSE",
    "long_threshold": 0.00050, "short_threshold": -0.00050, "no_trade_zone": "(-0.00050, 0.00050)",
    "cost_assumptions": {"spread_points": 1.0, "slippage_points_each_side": 2.0, "point_size": 0.00001}}


def assert_frozen_dataset(sessions, expected_hash: str) -> DatasetManifest:
    with sessions() as session:
        manifest = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == "research-dataset-v1",
            DatasetManifest.state == "FROZEN").order_by(DatasetManifest.frozen_at.desc()))
        if not manifest or manifest.content_hash != expected_hash:
            raise ValueError("Brain-v1 training requires the selected immutable frozen dataset hash.")
        return manifest


REQUIRED_FEATURES = tuple(item["name"] for item in feature_definitions())

def _numeric(values: dict) -> dict[str, float]:
    mapping = {"ASIA": 0., "LONDON": 1., "NEW_YORK": 2., "OFF_HOURS": 3., "DOWN": -1., "FLAT": 0., "UP": 1.}
    result = {}
    for key in REQUIRED_FEATURES:
        value = values.get(key)
        if isinstance(value, (int, float)) and value is not None: result[key] = float(value)
        elif value in mapping: result[key] = mapping[value]
        else: raise ValueError(f"Required Feature Set v1 value is invalid: {key}")
    return result


class BrainV1Dataset:
    """Labels are computed separately from raw future closes and never written into FeatureSnapshot."""
    def __init__(self, sessions, expected_hash: str) -> None: self.sessions, self.expected_hash = sessions, expected_hash
    def rows(self) -> tuple[list[dict], tuple[str, ...], dict]:
        manifest = assert_frozen_dataset(self.sessions, self.expected_hash)
        with self.sessions() as session:
            joined = session.execute(select(DatasetRow, FeatureSnapshot).join(FeatureSnapshot,
                FeatureSnapshot.snapshot_id == DatasetRow.feature_snapshot_id).where(DatasetRow.dataset_id == manifest.dataset_id)
                .order_by(DatasetRow.decision_at)).all()
            bars = list(session.scalars(select(RawMarketBar).where(RawMarketBar.timeframe == "M5").order_by(RawMarketBar.symbol, RawMarketBar.timestamp)))
        market = {(bar.symbol, bar.timestamp): bar for bar in bars}; grouped = defaultdict(list)
        for member, snapshot in joined: grouped[member.symbol].append((member, snapshot))
        rows, exclusions, feature_names = [], Counter(), None
        for symbol, items in grouped.items():
          for index, (member, snapshot) in enumerate(items):
            features = _numeric(snapshot.values)
            if feature_names is None: feature_names = tuple(sorted(features))
            if tuple(sorted(features)) != feature_names: continue
            future_index = index + LABEL_SPEC_V1["horizon_bars"]
            if future_index >= len(items): exclusions[("LABEL_HORIZON_UNAVAILABLE", symbol, member.split)] += 1; continue
            future_member, future_snapshot = items[future_index]
            if future_member.split != member.split: exclusions[("LABEL_HORIZON_CROSSES_SPLIT", symbol, member.split)] += 1; continue
            target_at = future_snapshot.decision_at
            future_bar = market.get((symbol, future_snapshot.raw_data_cutoff)); cutoff_bar = market.get((symbol, snapshot.raw_data_cutoff))
            if future_bar is None or cutoff_bar is None: exclusions[("LABEL_HORIZON_UNAVAILABLE", symbol, member.split)] += 1; continue
            future, entry = future_bar.close, cutoff_bar.close
            target = "LONG" if (future-entry)/entry >= LABEL_SPEC_V1["long_threshold"] else "SHORT" if (future-entry)/entry <= LABEL_SPEC_V1["short_threshold"] else "NO_TRADE"
            rows.append({"symbol": symbol, "timestamp": snapshot.decision_at, "raw_data_cutoff": snapshot.raw_data_cutoff, "target_timestamp": target_at,
                "feature_snapshot_id": snapshot.snapshot_id, "feature_version": snapshot.feature_version, "features": features,
                "target": target, "split": member.split, "target_raw_data_cutoff": future_snapshot.raw_data_cutoff,
                "entry_open": market.get((symbol, snapshot.decision_at)).open if market.get((symbol, snapshot.decision_at)) else None,
                "exit_close": future_bar.close})
        counts = {split: dict(Counter(row["target"] for row in rows if row["split"] == split)) for split in ("TRAIN", "VALIDATION", "OOS")}
        return rows, feature_names or (), {"manifest": manifest, "class_distribution": counts, "exclusions": {"|".join(key): value for key, value in exclusions.items()}}
