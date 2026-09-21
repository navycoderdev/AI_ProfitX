"""Read-only 22-feature and fixed-label BTCUSD preflight on broker-tagged bars."""

import json
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai.brain_v1 import REQUIRED_FEATURES, _numeric
from config.settings import get_settings
from data.candle_builder import TIMEFRAME_SECONDS
from data.storage import RawMarketDataRepository
from database.session import initialize_database
from features.engine import FeatureEngine
from research.dataset_v1 import ResearchDatasetV1Builder
from research.label_audit import audit_fixed_labels


SOURCE = "MT5:ICMarketsSC-Demo"
CONTEXTS = ("M15", "M30", "H1", "H4")


def main() -> None:
    settings = get_settings()
    if settings.allow_live_trading or settings.trading_mode.value == "LIVE":
        raise RuntimeError("Feature and label audit requires LIVE disabled.")
    repository = RawMarketDataRepository(initialize_database(settings))
    until = datetime.max.replace(tzinfo=timezone.utc)
    bars = repository.bars_as_of("BTCUSD", "M5", until, source=SOURCE)
    contexts = {tf: repository.bars_as_of("BTCUSD", tf, until, source=SOURCE) for tf in CONTEXTS}
    if not bars or any(not rows for rows in contexts.values()):
        raise RuntimeError("Broker-tagged BTCUSD history is incomplete.")
    times = {tf: [bar.timestamp for bar in rows] for tf, rows in contexts.items()}
    engine = FeatureEngine()
    candidates = []
    excluded = Counter()
    future_context = 0
    for index, bar in enumerate(bars):
        decision_at = bar.timestamp + timedelta(minutes=5)
        if index < 50:
            excluded["WARMUP"] += 1
            continue
        positions = {tf: bisect_right(times[tf], decision_at - timedelta(seconds=TIMEFRAME_SECONDS[tf]))
                     for tf in CONTEXTS}
        closed = {tf: rows[max(0, positions[tf] - 2):positions[tf]] for tf, rows in contexts.items()}
        if any(len(rows) < 2 for rows in closed.values()):
            excluded["ALIGNMENT_CONTEXT_UNAVAILABLE"] += 1
            continue
        values = engine.calculate(bars[max(0, index - 99):index + 1], bar.timestamp, closed["M15"])
        for tf, rows in closed.items():
            values[f"{tf.lower()}_trend"] = "UP" if rows[-1].close > rows[-2].close else "DOWN" if rows[-1].close < rows[-2].close else "FLAT"
            if rows[-1].timestamp + timedelta(seconds=TIMEFRAME_SECONDS[tf]) > decision_at:
                future_context += 1
        values["feature_version"] = "feature-set-v1"
        try:
            numeric = _numeric(values)
        except ValueError:
            excluded["FEATURE_NULL_POLICY"] += 1
            continue
        if tuple(sorted(numeric)) != tuple(sorted(REQUIRED_FEATURES)):
            raise RuntimeError("BTCUSD feature schema/order differs from locked 22-feature contract.")
        candidates.append({"symbol": "BTCUSD", "decision_time": decision_at.isoformat(),
                           "raw_data_cutoff": bar.timestamp.isoformat(), "values": values})
    if future_context or len(candidates) < 1000:
        raise RuntimeError("Feature leakage or insufficient BTCUSD candidate history.")
    boundaries = ResearchDatasetV1Builder.split_boundaries(candidates)
    for row in candidates:
        row["split"] = ResearchDatasetV1Builder.split_for_time(datetime.fromisoformat(row["decision_time"]), boundaries)
    close = {("BTCUSD", bar.timestamp): bar.close for bar in bars}
    labels = audit_fixed_labels(candidates, close)
    counts = {split: len([row for row in candidates if row["split"] == split]) for split in ("TRAIN", "VALIDATION", "OOS")}
    report = {"broker_server": "ICMarketsSC-Demo", "source": SOURCE, "symbol": "BTCUSD",
              "feature_set_version": "feature-set-v1", "feature_count": len(REQUIRED_FEATURES),
              "feature_candidates": len(candidates), "feature_exclusions": dict(excluded),
              "future_context_count": future_context, "feature_leakage": "PASS",
              "split_boundaries": {"validation_start": boundaries[0].isoformat(), "oos_start": boundaries[1].isoformat()},
              "candidate_split_counts": counts, "fixed_label_audit": labels,
              "label_distribution_gate": "PASS" if labels["usable_for_training"] else "BLOCKED_PATHOLOGICAL_LABELS",
              "legacy_fx_cost_assumptions_applicable": False,
              "broker_point_size": 0.01,
              "dataset_v4_frozen": False, "orders_submitted": 0}
    Path("reports/ic_btcusd_feature_label_audit.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("fixed_label_audit",)}, indent=2, sort_keys=True))
    print(json.dumps({"fixed_label_audit": labels}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
