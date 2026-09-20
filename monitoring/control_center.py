import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from config.settings import Settings
from database.models import (AuditLog, BacktestRun, DatasetManifest, DatasetRow, DecisionMemory, FeatureDefinition, FeatureSnapshot, MarketSnapshot, ModelInferenceLog, ModelVersion, RawMarketBar, RawMarketTick, RegimeHistory,
                             TradeMemory)
from database.session import check_database
from models.registry import ModelRegistry
from monitoring.alerts import AlertService
from monitoring.control import EmergencyStopService
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


class ControlCenter:
    """Read model for the dashboard; it does not issue trading commands except explicit emergency controls."""
    def __init__(self, settings: Settings, sessions: sessionmaker[Session], registry: ModelRegistry, alerts: AlertService,
                 emergency: EmergencyStopService, health: Callable[[], dict], positions: Callable[[], list[dict]] | None = None,
                 account: Callable[[], dict] | None = None, live_market: Callable[[], list[dict]] | None = None) -> None:
        self.settings, self.sessions, self.registry, self.alerts, self.emergency, self.health = settings, sessions, registry, alerts, emergency, health
        self.positions, self.account, self.live_market_source = positions or (lambda: []), account or (lambda: {}), live_market
    def overview(self) -> dict:
        account, positions = self.account(), self.positions(); production = self.registry.production()
        equity, high_water = float(account.get("equity", 0) or 0), float(account.get("high_water_equity", account.get("equity", 0)) or 0)
        return {"mt5_status": self.health().get("mt5"), "environment": self.settings.app_env.value, "mode": self.settings.trading_mode.value,
                "active_model": production["model_id"] if production else None, "balance": account.get("balance"), "equity": account.get("equity"),
                "margin": account.get("margin"), "daily_pnl": account.get("daily_pnl"), "open_exposure": sum(abs(float(item.get("volume", 0)) * float(item.get("price_current", item.get("price_open", 0)))) for item in positions),
                "drawdown": (high_water - equity) / high_water if high_water else None, "risk_status": "EMERGENCY_STOP" if self.emergency.status().get("active") else "NORMAL"}
    def live_market(self) -> list[dict]:
        if self.live_market_source and self.health().get("mt5"):
            observations = self.live_market_source()
            if observations:
                return observations
        with self.sessions() as session:
            ticks = list(session.scalars(select(RawMarketTick).order_by(RawMarketTick.id.desc())))
            regimes = list(session.scalars(select(RegimeHistory).order_by(RegimeHistory.id.desc())))
        latest_ticks, latest_regimes = {}, {}
        for item in ticks: latest_ticks.setdefault(item.symbol, item)
        for item in regimes: latest_regimes.setdefault(item.symbol, item)
        return [{"symbol": symbol, "bid": tick.bid, "ask": tick.ask, "spread": tick.ask - tick.bid, "timestamp": tick.timestamp,
                 "timeframe": latest_regimes.get(symbol).timeframe if symbol in latest_regimes else None,
                 "regime": latest_regimes.get(symbol).label if symbol in latest_regimes else "UNCERTAIN",
                 "volatility": (latest_regimes.get(symbol).measurements or {}).get("volatility_percentile") if symbol in latest_regimes else None}
                for symbol, tick in latest_ticks.items()]
    def ai_brain(self) -> dict:
        with self.sessions() as session:
            decision = session.scalar(select(DecisionMemory).order_by(DecisionMemory.id.desc()))
            inference = session.scalar(select(ModelInferenceLog).order_by(ModelInferenceLog.id.desc()))
            snapshot = session.scalar(select(MarketSnapshot).where(MarketSnapshot.snapshot_id == decision.market_snapshot_id)) if decision else None
            candidates = list(session.scalars(select(ModelVersion).where(ModelVersion.stage.in_(("CANDIDATE", "VALIDATING", "PAPER", "SHADOW")))))
            simulations = self._oos_simulations(session)
        production = self.registry.production()
        return {"active_brain": production["model_id"] if production else None, "model_status": production["status"] if production else None,
                "latest_decision": None if not decision else {"direction": decision.direction, "confidence": decision.confidence,
                    "model_version": decision.model_version, "feature_version": decision.feature_version,
                    "regime": snapshot.regime if snapshot else None, "timestamp": decision.timestamp},
                "last_inference": inference.timestamp if inference else None,
                "candidates": [{"model_id": item.version, "status": item.stage,
                    "validation_metrics": json.loads(item.metadata_json).get("validation_metrics"),
                    "out_of_sample_metrics": json.loads(item.metadata_json).get("out_of_sample_metrics"),
                    "confidence_policy": json.loads(item.metadata_json).get("confidence_policy"),
                    "feature_importance": json.loads(item.metadata_json).get("feature_importance"),
                    "oos_simulation": simulations.get(item.version)} for item in candidates]}
    def positions_view(self) -> list[dict]: return self.positions()
    def trade_memory(self, limit: int = 100) -> list[dict]:
        with self.sessions() as session:
            decisions = list(session.scalars(select(DecisionMemory).order_by(DecisionMemory.id.desc()).limit(limit)))
            trades = {item.trade_id: item for item in session.scalars(select(TradeMemory))}
        return [{"decision_id": item.decision_id, "trade_id": item.trade_id, "direction": item.direction, "confidence": item.confidence,
                 "model_version": item.model_version, "feature_version": item.feature_version, "risk": item.risk_metadata,
                 "outcome": trades[item.trade_id].outcome if item.trade_id in trades else None,
                 "mfe": (trades[item.trade_id].outcome or {}).get("mfe") if item.trade_id in trades else None,
                 "mae": (trades[item.trade_id].outcome or {}).get("mae") if item.trade_id in trades else None} for item in decisions]
    def model_lab(self) -> dict:
        with self.sessions() as session:
            rows = list(session.scalars(select(ModelVersion).order_by(ModelVersion.id)))
            simulations = self._oos_simulations(session)
        models = [{"model_id": item.version, "status": item.stage, **json.loads(item.metadata_json),
                   "oos_simulation": simulations.get(item.version)} for item in rows]
        return {"production_champion": self.registry.production(), "models": models,
            "supported_research_universe": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
            "unavailable_on_current_broker": {"BTCUSD": "UNAVAILABLE_ON_CURRENT_BROKER",
                "ETHUSD": "UNAVAILABLE_ON_CURRENT_BROKER"}}

    @staticmethod
    def _oos_simulations(session) -> dict:
        rows = session.scalars(select(BacktestRun).where(BacktestRun.symbol == "ALL").order_by(BacktestRun.created_at.desc())).all()
        result = {}
        for row in rows:
            if row.results.get("trade_scope") == "OFFLINE_OOS_SIMULATION_TRADES" and row.strategy_version not in result:
                result[row.strategy_version] = {"run_id": row.run_id, "simulation_timestamp": row.created_at,
                                                **row.results}
        return result
    def risk_center(self) -> dict:
        with self.sessions() as session:
            events = list(session.scalars(select(AuditLog).where(AuditLog.action.like("risk.%")).order_by(AuditLog.id.desc()).limit(100)))
        return {"emergency_stop": self.emergency.status(), "risk_events": [{"action": item.action, "payload": item.payload, "timestamp": item.created_at} for item in events],
                "alerts": [item for item in self.alerts.recent(100) if item["code"] in {"ABNORMAL_SPREAD", "STATE_MISMATCH", "MT5_DISCONNECTED"}]}
    def system_health(self) -> dict:
        health = self.health(); last_tick = None
        with self.sessions() as session: tick = session.scalar(select(RawMarketTick).order_by(RawMarketTick.id.desc()))
        if tick: last_tick = tick.timestamp
        # PAPER/SHADOW can read directly from MT5 before a background collector is started.
        # Treat a current terminal quote as a healthy feed; do not claim a stale database tick instead.
        if health.get("mt5") and self.live_market_source:
            observations = self.live_market_source()
            timestamps = [item.get("timestamp") for item in observations if item.get("timestamp")]
            if timestamps:
                last_tick = max(timestamps)
        if last_tick is not None and last_tick.tzinfo is None: last_tick = last_tick.replace(tzinfo=timezone.utc)
        return {**health, "last_successful_tick": last_tick, "data_feed_stale": last_tick is None or (datetime.now(timezone.utc) - last_tick).total_seconds() > self.settings.data_feed_stale_seconds,
                "background_workers": "external_orchestrator_required", "recent_alerts": self.alerts.recent(20)}
    def audit_log(self, limit: int = 200) -> list[dict]:
        with self.sessions() as session: rows = session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
        return [{"action": row.action, "environment": row.environment, "session_id": row.session_id, "payload": row.payload, "timestamp": row.created_at} for row in rows]

    def research_data(self) -> dict:
        from research.governance import DatasetSpec, dependency_audit, reason_codes_from_quality
        from sqlalchemy import func
        with self.sessions() as session:
            rows = session.execute(select(RawMarketBar.symbol, RawMarketBar.timeframe, func.count(RawMarketBar.id),
                func.min(RawMarketBar.timestamp), func.max(RawMarketBar.timestamp)).group_by(RawMarketBar.symbol, RawMarketBar.timeframe)).all()
        reports = sorted(Path("reports/data_quality").glob("aggregate_*.json"), key=lambda path: path.stat().st_mtime)
        aggregate = json.loads(reports[-1].read_text(encoding="utf-8")) if reports else None
        qualities = {(item["symbol"], item["timeframe"]): item for item in (aggregate or {}).get("coverage", [])}
        coverage = []
        for symbol, timeframe, count, first, last in rows:
            details = qualities.get((symbol, timeframe), {})
            coverage.append({"symbol": symbol, "broker_symbol": details.get("broker_symbol", symbol),
                "timeframe": timeframe, "count": count, "first": first, "last": last,
                "quality": details.get("status", "UNRESOLVED"),
                # Older persisted reports predate the field; adapt them through the same central policy.
                "reason_codes": details.get("reason_codes") or reason_codes_from_quality(details), "details": details})
        spec = DatasetSpec()
        with self.sessions() as session:
            manifest = session.scalar(select(DatasetManifest).where(DatasetManifest.state == "FROZEN")
                .order_by(DatasetManifest.frozen_at.desc()))
            if manifest is None:
                manifest = session.scalar(select(DatasetManifest).order_by(DatasetManifest.id.desc()))
            definitions = list(session.scalars(select(FeatureDefinition).where(FeatureDefinition.active.is_(True))))
            active_snapshot_count = int(session.scalar(select(func.count(DatasetRow.id)).where(DatasetRow.dataset_id == manifest.dataset_id)) or 0) if manifest else None
            examples = []
            if manifest:
                example_rows = session.execute(select(DatasetRow, FeatureSnapshot).join(FeatureSnapshot,
                    FeatureSnapshot.snapshot_id == DatasetRow.feature_snapshot_id).where(DatasetRow.dataset_id == manifest.dataset_id)
                    .order_by(DatasetRow.decision_at).limit(3)).all()
                examples = [{"snapshot_id": snap.snapshot_id, "symbol": row.symbol, "decision_at": row.decision_at,
                    "split": row.split, "raw_data_cutoff": snap.raw_data_cutoff, "values": snap.values} for row, snap in example_rows]
        dataset = {"dataset_version": manifest.dataset_version if manifest else spec.version, "dataset_state": manifest.state if manifest else "NOT_BUILT",
            "execution_timeframe": spec.execution_timeframe, "context_timeframes": list(spec.context_timeframes),
            "decision_semantics": spec.decision_semantics, "alignment_policy": spec.alignment_policy,
            "feature_set_version": manifest.feature_set_version if manifest else spec.feature_set_version,
            "split_policy_version": manifest.split_policy_version if manifest else spec.split_policy_version,
            "content_hash": manifest.content_hash if manifest else None,
            "candidate_rows": manifest.candidate_rows if manifest else None, "usable_rows": manifest.usable_rows if manifest else None,
            "excluded_rows": manifest.excluded_rows if manifest else None, "quarantined_rows": manifest.quarantined_rows if manifest else None,
            "train_rows": manifest.train_rows if manifest else None, "validation_rows": manifest.validation_rows if manifest else None,
            "oos_rows": manifest.oos_rows if manifest else None, "source_start": manifest.source_start if manifest else None,
            "source_end": manifest.source_end if manifest else None, "leakage_status": "PASS" if manifest and manifest.state == "FROZEN" else "NOT_RUN",
            "feature_count": len(definitions), "feature_snapshots": active_snapshot_count, "feature_snapshot_examples": examples}
        return {"total_candles": sum(item["count"] for item in coverage), "symbols": sorted({item["symbol"] for item in coverage}),
            "supported_research_universe": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
            "unavailable_on_current_broker": {"BTCUSD": "UNAVAILABLE_ON_CURRENT_BROKER",
                "ETHUSD": "UNAVAILABLE_ON_CURRENT_BROKER"},
            "timeframes": sorted({item["timeframe"] for item in coverage}), "aggregate": aggregate, "coverage": coverage,
            "dataset": dataset, "dependency_audit": dependency_audit(definitions)}

    def backtest_runs(self) -> list[dict]:
        with self.sessions() as session: rows = session.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc())).all()
        return [{"run_id": row.run_id, "strategy_version": row.strategy_version, "symbol": row.symbol, "timeframe": row.timeframe,
            "configuration": row.configuration, "metrics": row.results.get("metrics", {}), "metadata": row.results.get("metadata", {}),
            "created_at": row.created_at} for row in rows]
