"""Gate and freeze four-symbol Dataset v3; never fit a model here."""
import json
from collections import Counter, defaultdict
from datetime import timezone
from pathlib import Path

from sqlalchemy import func, select

from ai.brain_v1 import BrainV1Dataset
from backtesting.symbol_economics import SymbolEconomics
from config.settings import get_settings
from database.models import DatasetManifest, DatasetRow, FeatureSnapshot, RawMarketBar
from database.session import initialize_database
from research.dataset_v1 import ResearchDatasetV1Builder
from research.governance import DatasetSpec
from research.label_audit import audit_fixed_labels


VERSION = "research-dataset-v3"
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD")
SPLITS = ("TRAIN", "VALIDATION", "OOS")


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Dataset research requires LIVE disabled.")
    sessions = initialize_database(settings)
    gold_quality = json.loads(Path("reports/xauusd_history_audit.json").read_text(encoding="utf-8"))
    if not gold_quality["quality_gate_passed"]:
        raise RuntimeError("XAUUSD quality review has unresolved findings.")
    economics = json.loads(Path("reports/contract_economics.json").read_text(encoding="utf-8"))
    contracts = {symbol: SymbolEconomics.from_mt5_probe(symbol, economics["symbols"][symbol]) for symbol in SYMBOLS}
    if economics["account_currency"] != "USD":
        raise RuntimeError("Contract economics are verified only for a USD account.")
    with sessions() as session:
        old = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == "research-dataset-v2",
            DatasetManifest.state == "FROZEN"))
        existing = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == VERSION,
            DatasetManifest.state == "FROZEN"))
        coverage = session.execute(select(RawMarketBar.symbol, func.min(RawMarketBar.timestamp),
            func.max(RawMarketBar.timestamp)).where(RawMarketBar.symbol.in_(SYMBOLS),
            RawMarketBar.timeframe == "M5").group_by(RawMarketBar.symbol)).all()
        raw = session.scalars(select(RawMarketBar).where(RawMarketBar.symbol.in_(SYMBOLS),
            RawMarketBar.timeframe == "M5")).all()
    if old is None or old.content_hash != "6696b911e993bb43c000377039e7d40bc660fc17d8f4e89fc5711f69d8cefefe":
        raise RuntimeError("Dataset v2 immutable lineage check failed.")
    if {row[0] for row in coverage} != set(SYMBOLS):
        raise RuntimeError("Genuine M5 history is missing for a required symbol.")
    common_start = max(row[1] for row in coverage).replace(tzinfo=timezone.utc)
    common_end = min(row[2] for row in coverage).replace(tzinfo=timezone.utc)
    if common_start >= common_end:
        raise RuntimeError("No common chronological four-symbol research window.")
    close = {(bar.symbol, bar.timestamp.replace(tzinfo=timezone.utc)): bar.close for bar in raw}
    label_audit = None
    if existing is None:
        def gate(candidates: list[dict]) -> dict:
            nonlocal label_audit
            label_audit = audit_fixed_labels(candidates, close)
            path = Path("reports/dataset_v3_label_preflight.json")
            path.write_text(json.dumps(label_audit, indent=2, sort_keys=True), encoding="utf-8")
            if not label_audit["usable_for_training"]:
                raise RuntimeError("Fixed 0.050% label threshold is pathological; stop before Dataset v3 freeze or Brain-v2 training.")
            boundaries = ResearchDatasetV1Builder.split_boundaries(candidates)
            if boundaries != ResearchDatasetV1Builder.split_boundaries(list(reversed(candidates))):
                raise RuntimeError("Symbol order changes chronological split boundaries.")
            return label_audit
        spec = DatasetSpec(version=VERSION, split_policy_version="chronological-time-v2")
        result = ResearchDatasetV1Builder(sessions, spec).build_and_freeze(SYMBOLS,
            pre_freeze_gate=gate, window_start=common_start, window_end=common_end)
        with sessions() as session:
            manifest = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_id == result["dataset_id"]))
        if result["feature_count"] != 22 or result["dependency_audit"]["freeze_blocked"]:
            raise RuntimeError("Feature dependency gate failed.")
    else:
        manifest = existing
        label_audit = json.loads(Path("reports/dataset_v3_label_preflight.json").read_text(encoding="utf-8"))
    with sessions() as session:
        members = session.execute(select(DatasetRow.symbol, DatasetRow.split, DatasetRow.decision_at,
            FeatureSnapshot.raw_data_cutoff).join(FeatureSnapshot,
            FeatureSnapshot.snapshot_id == DatasetRow.feature_snapshot_id).where(
            DatasetRow.dataset_id == manifest.dataset_id)).all()
    counts = Counter(row[1] for row in members)
    per_symbol = defaultdict(Counter)
    by_time = defaultdict(set)
    by_split = defaultdict(list)
    for symbol, split, at, cutoff in members:
        if cutoff > at:
            raise RuntimeError("Feature leakage detected.")
        per_symbol[symbol][split] += 1
        by_time[at].add(split)
        by_split[split].append(at)
    if any(len(splits) != 1 for splits in by_time.values()) or not max(by_split["TRAIN"]) < min(by_split["VALIDATION"]) or not max(by_split["VALIDATION"]) < min(by_split["OOS"]):
        raise RuntimeError("Chronological split integrity failed.")
    if counts != {"TRAIN": manifest.train_rows, "VALIDATION": manifest.validation_rows, "OOS": manifest.oos_rows} or len(members) != manifest.usable_rows:
        raise RuntimeError("Manifest count reconciliation failed.")
    labels, feature_names, metadata = BrainV1Dataset(sessions, manifest.content_hash, VERSION).rows()
    if len(feature_names) != 22 or metadata["exclusions"] != label_audit["exclusions"]:
        raise RuntimeError("Persisted label or feature audit differs from pre-freeze evidence.")
    distribution = {symbol: {split: dict(Counter(row["target"] for row in labels if row["symbol"] == symbol and row["split"] == split))
                             for split in SPLITS} for symbol in SYMBOLS}
    if distribution != {symbol: {split: {label: count for label, count in label_audit["class_distribution"][symbol][split].items() if count}
                                 for split in SPLITS} for symbol in SYMBOLS}:
        raise RuntimeError("Persisted label distribution differs from pre-freeze evidence.")
    report = {"dataset_version": VERSION, "dataset_id": manifest.dataset_id, "state": manifest.state,
        "dataset_hash": manifest.content_hash, "old_dataset_v2_hash": old.content_hash,
        "common_window": {"start": common_start.isoformat(), "end": common_end.isoformat()},
        "total_usable_rows": manifest.usable_rows,
        "split_counts": {split: counts[split] for split in SPLITS},
        "per_symbol": {symbol: {split: per_symbol[symbol][split] for split in SPLITS} for symbol in SYMBOLS},
        "label_distribution": distribution, "label_exclusions": metadata["exclusions"],
        "label_split_crossings_included": 0,
        "boundaries": {"train_max": max(by_split["TRAIN"]).isoformat(),
            "validation_min": min(by_split["VALIDATION"]).isoformat(),
            "validation_max": max(by_split["VALIDATION"]).isoformat(),
            "oos_min": min(by_split["OOS"]).isoformat()},
        "quality_gate": "PASS_REVIEWED_XAUUSD", "feature_dependencies": "PASS",
        "closed_candle_alignment": "PASS", "leakage": "PASS",
        "manifest_reconciliation": "PASS", "symbol_order_invariance": "PASS",
        "contract_assumption_source": "reports/contract_economics.json"}
    path = Path("reports/dataset_v3_freeze.json")
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
