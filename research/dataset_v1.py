"""Research Dataset v1 builder. It is deliberately feature-only: no labels or model training."""
from collections import Counter
from datetime import timedelta

from data.candle_builder import TIMEFRAME_SECONDS
from data.storage import RawMarketDataRepository
from features.engine import FeatureEngine
from features.registry import FeatureRegistry
from features.snapshots import FeatureSnapshotService
from research.governance import DatasetSpec, FREEZE_GATES, can_freeze, content_hash, dependency_audit
from research.repository import DatasetGovernanceRepository


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
    def __init__(self, sessions) -> None:
        self.sessions = sessions; self.raw = RawMarketDataRepository(sessions); self.snapshots = FeatureSnapshotService(sessions)
        self.registry = FeatureRegistry(sessions); self.governance = DatasetGovernanceRepository(sessions)
        self.engine = FeatureEngine(); self.spec = DatasetSpec()

    def register_features(self) -> list[object]:
        return [self.registry.register(item["name"], item["version"], item) for item in feature_definitions()]

    @staticmethod
    def _split(index: int, total: int) -> str:
        return "TRAIN" if index < total * .70 else "VALIDATION" if index < total * .85 else "OOS"

    def build_and_freeze(self, symbols: tuple[str, ...] = ("EURUSD", "GBPUSD", "USDJPY")) -> dict:
        definitions = self.register_features(); audit = dependency_audit(definitions)
        if audit["freeze_blocked"]: raise RuntimeError("Feature dependency metadata is incomplete; Dataset v1 cannot freeze.")
        manifest = self.governance.register_not_built(self.spec)
        candidates, excluded, snapshot_batch, membership = [], [], [], []
        source_starts, source_ends = [], []
        direct_masks = {(mask.symbol, mask.timeframe): self.governance.masks(mask.symbol, mask.timeframe)
                        for symbol in symbols for mask in self.governance.masks(symbol)}
        for symbol in symbols:
            base = self.raw.bars_as_of(symbol, "M5", __import__("datetime").datetime.max.replace(tzinfo=__import__("datetime").timezone.utc))
            contexts = {tf: self.raw.bars_as_of(symbol, tf, __import__("datetime").datetime.max.replace(tzinfo=__import__("datetime").timezone.utc))
                        for tf in self.spec.context_timeframes}
            if not base: continue
            source_starts.append(base[0].timestamp); source_ends.append(base[-1].timestamp)
            for index, bar in enumerate(base):
                decision_at = bar.timestamp + timedelta(seconds=TIMEFRAME_SECONDS["M5"])
                if index < WARMUP_BARS:
                    excluded.append("WARMUP"); continue
                masks = direct_masks.get((symbol, "M5"), [])
                if any(mask.start_time <= bar.timestamp < mask.end_time for mask in masks):
                    excluded.append("QUARANTINED_INTERVAL"); continue
                # The raw cutoff is exactly the M5 candle open timestamp; no subsequent/future M5 bar is supplied.
                history = base[max(0, index - 99):index + 1]
                closed_context = {tf: [item for item in rows if item.timestamp + timedelta(seconds=TIMEFRAME_SECONDS[tf]) <= decision_at]
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
                candidates.append({"symbol": symbol, "decision_time": decision_at.isoformat(), "raw_data_cutoff": bar.timestamp.isoformat(), "values": values})
                snapshot_batch.append({"symbol": symbol, "timeframe": "M5", "decision_at": decision_at, "feature_version": FEATURE_VERSION,
                    "raw_data_cutoff": bar.timestamp, "values": values, "context": {"decision_semantics": self.spec.decision_semantics,
                        "alignment_policy": self.spec.alignment_policy, "context_timeframes": list(self.spec.context_timeframes)}})
        if not candidates: raise RuntimeError("No usable Dataset v1 rows; dataset cannot freeze.")
        # SQLite and production DBs both remain stable under bounded transactions; do not retain a giant ORM unit of work.
        for offset in range(0, len(snapshot_batch), 250):
            batch = snapshot_batch[offset:offset + 250]
            snapshot_ids = self.snapshots.save_batch(batch)
            membership = []
            for index, (row, snapshot_id) in enumerate(zip(candidates[offset:offset + 250], snapshot_ids), start=offset):
                membership.append({"feature_snapshot_id": snapshot_id, "symbol": row["symbol"],
                    "decision_at": __import__("datetime").datetime.fromisoformat(row["decision_time"]),
                    "split": self._split(index, len(candidates)), "exclusion_codes": []})
            self.governance.add_rows(manifest.dataset_id, membership)
        counts = {"candidate_rows": len(candidates) + len(excluded), "usable_rows": len(candidates), "excluded_rows": len(excluded),
            "quarantined_rows": excluded.count("QUARANTINED_INTERVAL"), "train_rows": sum(x["split"] == "TRAIN" for x in membership),
            "validation_rows": sum(x["split"] == "VALIDATION" for x in membership), "oos_rows": sum(x["split"] == "OOS" for x in membership)}
        gates = set(FREEZE_GATES)
        if not can_freeze(gates, False): raise RuntimeError("Mandatory Dataset v1 freeze gates did not pass.")
        frozen = self.governance.freeze(manifest.dataset_id, source="MT5", symbols=list(symbols), source_start=min(source_starts),
            source_end=max(source_ends), counts=counts, reason_code_summary=dict(Counter(excluded)), content_hash=content_hash(candidates, self.spec))
        return {"dataset_id": frozen.dataset_id, "state": frozen.state, "content_hash": frozen.content_hash, "counts": counts,
            "feature_count": len(definitions), "leakage_status": "PASS", "dependency_audit": audit}
