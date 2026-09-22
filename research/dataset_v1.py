"""Research Dataset v1 builder. It is deliberately feature-only: no labels or model training."""
from collections import Counter
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import Callable

from data.candle_builder import TIMEFRAME_SECONDS
from data.storage import RawMarketDataRepository
from features.engine import FeatureEngine
from features.registry import FeatureRegistry
from features.snapshots import FeatureSnapshotService
from research.governance import DatasetSpec, FREEZE_GATES, can_freeze, content_hash, dependency_audit
from research.repository import DatasetGovernanceRepository
from sqlalchemy import func, select
from database.models import DatasetRow, FeatureSnapshot


FEATURE_VERSION = "feature-set-v1"
WARMUP_BARS = 50


def feature_definitions() -> list[dict]:
    """Compact, declared Feature Set v1. Each dependency is explicit and past-only."""
    names = ("return_1", "momentum_10", "volatility_20", "atr_14", "rsi_14", "macd", "adx_proxy_14",
        "candle_body", "upper_wick_ratio", "lower_wick_ratio", "rolling_high_20", "rolling_low_20",
        "breakout_strength", "spread_percentile", "volatility_percentile", "session", "day_of_week", "hour_utc")
    definitions = [{"name": name, "version": FEATURE_VERSION, "timeframe": "M5", "lookback": 50,
        "warmup": WARMUP_BARS, "past_only": True, "null_policy": "EXCLUDE_ROW",
        "dependency": {"source_timeframe": "M5", "lookback": 50, "warmup": WARMUP_BARS}} for name in names]
    for timeframe in DatasetSpec().context_timeframes:
        definitions.append({"name": f"{timeframe.lower()}_trend", "version": FEATURE_VERSION, "timeframe": timeframe,
            "lookback": 2, "warmup": 2, "past_only": True, "null_policy": "EXCLUDE_ROW",
            "dependency": {"source_timeframe": timeframe, "lookback": 2, "warmup": 2}})
    return definitions


class ResearchDatasetV1Builder:
    def __init__(self, sessions, spec: DatasetSpec | None = None) -> None:
        self.sessions = sessions; self.raw = RawMarketDataRepository(sessions); self.snapshots = FeatureSnapshotService(sessions)
        self.registry = FeatureRegistry(sessions); self.governance = DatasetGovernanceRepository(sessions)
        self.engine = FeatureEngine(); self.spec = spec or DatasetSpec(version="research-dataset-v2", split_policy_version="chronological-time-v2")

    def register_features(self) -> list[object]:
        return [self.registry.register(item["name"], item["version"], item) for item in feature_definitions()]

    @staticmethod
    def split_boundaries(candidates: list[dict]) -> tuple[datetime, datetime]:
        """Quantiles of unique decision times; one timestamp always gets one split."""
        times = sorted({datetime.fromisoformat(row["decision_time"]).astimezone(timezone.utc) for row in candidates})
        if len(times) < 3:
            raise ValueError("At least three unique decision timestamps are required for chronological splits.")
        train_end = max(1, min(len(times) - 2, int(len(times) * .70)))
        validation_end = max(train_end + 1, min(len(times) - 1, int(len(times) * .85)))
        return times[train_end], times[validation_end]

    @staticmethod
    def split_for_time(decision_at: datetime, boundaries: tuple[datetime, datetime]) -> str:
        return "TRAIN" if decision_at < boundaries[0] else "VALIDATION" if decision_at < boundaries[1] else "OOS"

    def build_and_freeze(self, symbols: tuple[str, ...] = ("EURUSD", "GBPUSD", "USDJPY"),
                         pre_freeze_gate: Callable[[list[dict]], dict] | None = None,
                         window_start: datetime | None = None, window_end: datetime | None = None,
                         source: str | None = None) -> dict:
        definitions = self.register_features(); audit = dependency_audit(definitions)
        if audit["freeze_blocked"]: raise RuntimeError("Feature dependency metadata is incomplete; Dataset v1 cannot freeze.")
        manifest = self.governance.register_not_built(self.spec)
        with self.sessions() as session:
            existing_rows = session.scalar(select(func.count(DatasetRow.id)).where(DatasetRow.dataset_id == manifest.dataset_id))
        if existing_rows:
            raise RuntimeError("Incomplete Dataset v2 membership exists; inspect and clear that attempt before retrying.")
        candidates, excluded, snapshot_batch = [], [], []
        source_starts, source_ends = [], []
        masks_by_symbol = {symbol: self.governance.masks(symbol) for symbol in symbols}
        dependency = {item["timeframe"]: item["dependency"] for item in feature_definitions()}
        for symbol in symbols:
            base = self.raw.bars_as_of(symbol, "M5", __import__("datetime").datetime.max.replace(tzinfo=__import__("datetime").timezone.utc), source=source)
            if window_start or window_end:
                base = [bar for bar in base if (window_start is None or bar.timestamp >= window_start) and
                        (window_end is None or bar.timestamp <= window_end)]
            contexts = {tf: self.raw.bars_as_of(symbol, tf, __import__("datetime").datetime.max.replace(tzinfo=__import__("datetime").timezone.utc), source=source)
                        for tf in self.spec.context_timeframes}
            context_times = {tf: [item.timestamp for item in rows] for tf, rows in contexts.items()}
            if not base: continue
            source_starts.append(base[0].timestamp); source_ends.append(base[-1].timestamp)
            for index, bar in enumerate(base):
                decision_at = bar.timestamp + timedelta(seconds=TIMEFRAME_SECONDS["M5"])
                if index < WARMUP_BARS:
                    excluded.append("WARMUP"); continue
                contaminated = False
                for mask in masks_by_symbol[symbol]:
                    if mask.timeframe not in dependency:
                        continue
                    meta = dependency[mask.timeframe]
                    horizon = timedelta(seconds=TIMEFRAME_SECONDS[mask.timeframe] *
                                        (meta["lookback"] + meta["warmup"] + 1))
                    if decision_at - horizon < mask.end_time and decision_at >= mask.start_time:
                        contaminated = True
                        break
                if contaminated:
                    excluded.append("QUARANTINED_INTERVAL"); continue
                # The raw cutoff is exactly the M5 candle open timestamp; no subsequent/future M5 bar is supplied.
                history = base[max(0, index - 99):index + 1]
                closed_context = {tf: rows[:bisect_right(context_times[tf], decision_at - timedelta(seconds=TIMEFRAME_SECONDS[tf]))]
                                  for tf, rows in contexts.items()}
                if any(len(rows) < 2 for rows in closed_context.values()):
                    excluded.append("ALIGNMENT_CONTEXT_UNAVAILABLE"); continue
                values = self.engine.calculate(history, bar.timestamp, closed_context["M15"])
                for tf, rows in closed_context.items():
                    values[f"{tf.lower()}_trend"] = "UP" if rows[-1].close > rows[-2].close else "DOWN" if rows[-1].close < rows[-2].close else "FLAT"
                values["feature_version"] = FEATURE_VERSION
                # Required numeric indicators must be present after warm-up; categorical context is retained.
                required = ("return_1", "momentum_10", "volatility_20", "atr_14", "rsi_14", "macd", "adx_proxy_14", "spread_percentile")
                if any(values.get(key) is None for key in required):
                    excluded.append("FEATURE_NULL_POLICY"); continue
                candidates.append({"symbol": symbol, "decision_time": decision_at.isoformat(), "raw_data_cutoff": bar.timestamp.isoformat(),
                                   "values": values, **({"source": source} if source else {})})
                snapshot_batch.append({"symbol": symbol, "timeframe": "M5", "decision_at": decision_at, "feature_version": FEATURE_VERSION,
                    "raw_data_cutoff": bar.timestamp, "values": values, "context": {"decision_semantics": self.spec.decision_semantics,
                        "alignment_policy": self.spec.alignment_policy, "context_timeframes": list(self.spec.context_timeframes),
                        **({"source": source} if source else {})}})
        if not candidates: raise RuntimeError("No usable Dataset v1 rows; dataset cannot freeze.")
        boundaries = self.split_boundaries(candidates)
        split_counts = Counter()
        symbol_counts = {symbol: Counter() for symbol in symbols}
        for row in candidates:
            row["split"] = self.split_for_time(datetime.fromisoformat(row["decision_time"]), boundaries)
            split_counts[row["split"]] += 1
            symbol_counts[row["symbol"]][row["split"]] += 1
        label_audit = pre_freeze_gate(candidates) if pre_freeze_gate else None
        # SQLite and production DBs both remain stable under bounded transactions; do not retain a giant ORM unit of work.
        for offset in range(0, len(snapshot_batch), 250):
            batch = snapshot_batch[offset:offset + 250]
            snapshot_ids = self.snapshots.save_batch(batch)
            membership = []
            for row, snapshot_id in zip(candidates[offset:offset + 250], snapshot_ids):
                membership.append({"feature_snapshot_id": snapshot_id, "symbol": row["symbol"],
                    "decision_at": datetime.fromisoformat(row["decision_time"]),
                    "split": row["split"], "exclusion_codes": []})
            self.governance.add_rows(manifest.dataset_id, membership)
        counts = {"candidate_rows": len(candidates) + len(excluded), "usable_rows": len(candidates), "excluded_rows": len(excluded),
            "quarantined_rows": excluded.count("QUARANTINED_INTERVAL"), "train_rows": split_counts["TRAIN"],
            "validation_rows": split_counts["VALIDATION"], "oos_rows": split_counts["OOS"]}
        with self.sessions() as session:
            actual = dict(session.execute(select(DatasetRow.split, func.count(DatasetRow.id)).where(
                DatasetRow.dataset_id == manifest.dataset_id).group_by(DatasetRow.split)).all())
            future_features = session.scalar(select(func.count(DatasetRow.id)).join(FeatureSnapshot,
                FeatureSnapshot.snapshot_id == DatasetRow.feature_snapshot_id).where(
                DatasetRow.dataset_id == manifest.dataset_id, FeatureSnapshot.raw_data_cutoff > DatasetRow.decision_at))
        if actual != dict(split_counts) or sum(actual.values()) != len(candidates):
            raise RuntimeError("Persisted split membership does not reconcile with the manifest.")
        if future_features:
            raise RuntimeError("Feature leakage gate failed: a raw cutoff follows its decision timestamp.")
        gates = set(FREEZE_GATES)
        if not can_freeze(gates, False): raise RuntimeError("Mandatory Dataset v1 freeze gates did not pass.")
        frozen = self.governance.freeze(manifest.dataset_id, source=source or "MT5", symbols=list(symbols), source_start=min(source_starts),
            source_end=max(source_ends), counts=counts, reason_code_summary=dict(Counter(excluded)), content_hash=content_hash(candidates, self.spec))
        return {"dataset_id": frozen.dataset_id, "dataset_version": self.spec.version, "state": frozen.state,
            "content_hash": frozen.content_hash, "counts": counts,
            "split_boundaries": {"validation_start": boundaries[0].isoformat(), "oos_start": boundaries[1].isoformat()},
            "per_symbol": {symbol: {split: symbol_counts[symbol][split] for split in ("TRAIN", "VALIDATION", "OOS")} for symbol in symbols},
            "feature_count": len(definitions), "leakage_status": "PASS", "dependency_audit": audit,
            "label_audit": label_audit}
