"""Gate and freeze immutable IC Markets Dataset v4; never fit a model here."""

import json
from collections import Counter, defaultdict
from datetime import timezone
from pathlib import Path

from sqlalchemy import func, select

from ai.brain_v1 import BrainV1Dataset
from config.settings import get_settings
from database.models import DatasetManifest, DatasetRow, FeatureSnapshot, RawMarketBar
from database.session import initialize_database
from research.dataset_v1 import ResearchDatasetV1Builder
from research.governance import DatasetSpec
from research.label_audit import audit_fixed_labels


VERSION = "research-dataset-v4"
SOURCE = "MT5:ICMarketsSC-Demo"
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "ETHUSD")
SPLITS = ("TRAIN", "VALIDATION", "OOS")


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Dataset research requires LIVE disabled.")
    quality = json.loads(Path("reports/ic_universe_quality.json").read_text(encoding="utf-8"))
    if quality["quality_gate"] != "PASS" or quality["source"] != SOURCE:
        raise RuntimeError("IC Markets universe quality gate has not passed.")
    sessions = initialize_database(settings)
    with sessions() as session:
        v3 = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == "research-dataset-v3",
            DatasetManifest.state == "FROZEN"))
        existing = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == VERSION,
            DatasetManifest.state == "FROZEN"))
        coverage = session.execute(select(RawMarketBar.symbol, func.min(RawMarketBar.timestamp),
            func.max(RawMarketBar.timestamp)).where(RawMarketBar.source == SOURCE,
            RawMarketBar.symbol.in_(SYMBOLS), RawMarketBar.timeframe == "M5").group_by(RawMarketBar.symbol)).all()
        raw = session.scalars(select(RawMarketBar).where(RawMarketBar.source == SOURCE,
            RawMarketBar.symbol.in_(SYMBOLS), RawMarketBar.timeframe == "M5")).all()
    if v3 is None or v3.content_hash != "790d8e18de1f8d8fa9e159a26b2cf8628fc813aa2a094b6909e84849f15f7de4":
        raise RuntimeError("Dataset v3 immutable lineage check failed.")
    if existing:
        raise RuntimeError("Dataset v4 is already frozen and cannot be rebuilt.")
    if {row[0] for row in coverage} != set(SYMBOLS):
        raise RuntimeError("A required IC Markets symbol lacks genuine M5 history.")
    common_start = max(row[1] for row in coverage).replace(tzinfo=timezone.utc)
    common_end = min(row[2] for row in coverage).replace(tzinfo=timezone.utc)
    close = {(bar.symbol, bar.timestamp.replace(tzinfo=timezone.utc)): bar.close for bar in raw}
    label_audit = None

    def gate(candidates: list[dict]) -> dict:
        nonlocal label_audit
        label_audit = audit_fixed_labels(candidates, close)
        Path("reports/dataset_v4_label_preflight.json").write_text(
            json.dumps(label_audit, indent=2, sort_keys=True), encoding="utf-8")
        if not label_audit["usable_for_training"]:
            raise RuntimeError("Fixed 0.050% label threshold is pathological for Dataset v4.")
        if ResearchDatasetV1Builder.split_boundaries(candidates) != ResearchDatasetV1Builder.split_boundaries(list(reversed(candidates))):
            raise RuntimeError("Symbol ordering changes chronological split boundaries.")
        return label_audit

    spec = DatasetSpec(version=VERSION, quality_policy_version="ic-universe-quality-v1",
        quarantine_policy_version="dataset-v4-ic-universe-quality-v1", split_policy_version="chronological-time-v2")
    result = ResearchDatasetV1Builder(sessions, spec).build_and_freeze(
        SYMBOLS, pre_freeze_gate=gate, window_start=common_start, window_end=common_end, source=SOURCE)
    with sessions() as session:
        manifest = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_id == result["dataset_id"]))
        members = session.execute(select(DatasetRow.symbol, DatasetRow.split, DatasetRow.decision_at,
            FeatureSnapshot.raw_data_cutoff, FeatureSnapshot.context).join(FeatureSnapshot,
            FeatureSnapshot.snapshot_id == DatasetRow.feature_snapshot_id).where(
            DatasetRow.dataset_id == manifest.dataset_id)).all()
    counts, per_symbol, by_time, by_split = Counter(), defaultdict(Counter), defaultdict(set), defaultdict(list)
    for symbol, split, at, cutoff, context in members:
        if cutoff > at or context.get("source") != SOURCE:
            raise RuntimeError("Feature leakage or broker lineage mismatch.")
        counts[split] += 1; per_symbol[symbol][split] += 1; by_time[at].add(split); by_split[split].append(at)
    if any(len(value) != 1 for value in by_time.values()) or not max(by_split["TRAIN"]) < min(by_split["VALIDATION"]) or not max(by_split["VALIDATION"]) < min(by_split["OOS"]):
        raise RuntimeError("Chronological split integrity failed.")
    if counts != {"TRAIN": manifest.train_rows, "VALIDATION": manifest.validation_rows, "OOS": manifest.oos_rows} or len(members) != manifest.usable_rows:
        raise RuntimeError("Manifest reconciliation failed.")
    labels, names, metadata = BrainV1Dataset(sessions, manifest.content_hash, VERSION).rows()
    if len(names) != 22 or metadata["exclusions"] != label_audit["exclusions"]:
        raise RuntimeError("Persisted label/feature accounting differs from preflight.")
    distribution = {symbol: {split: dict(Counter(row["target"] for row in labels
        if row["symbol"] == symbol and row["split"] == split)) for split in SPLITS} for symbol in SYMBOLS}
    report = {"dataset_version": VERSION, "state": manifest.state, "dataset_hash": manifest.content_hash,
        "source": SOURCE, "universe": list(SYMBOLS), "common_window": {"start": common_start.isoformat(), "end": common_end.isoformat()},
        "total_usable_rows": manifest.usable_rows, "split_counts": dict(counts),
        "per_symbol": {symbol: {split: per_symbol[symbol][split] for split in SPLITS} for symbol in SYMBOLS},
        "label_distribution": distribution, "label_exclusions": metadata["exclusions"],
        "boundaries": {"train_max": max(by_split["TRAIN"]).isoformat(), "validation_min": min(by_split["VALIDATION"]).isoformat(),
                       "validation_max": max(by_split["VALIDATION"]).isoformat(), "oos_min": min(by_split["OOS"]).isoformat()},
        "feature_count": len(names), "quality": "PASS_WITH_QUARANTINE", "leakage": "PASS",
        "manifest_reconciliation": "PASS", "symbol_order_invariance": "PASS", "orders_submitted": 0}
    Path("reports/dataset_v4_freeze.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
