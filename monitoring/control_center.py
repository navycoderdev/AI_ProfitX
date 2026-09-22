import json
import os
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from config.settings import Settings
from database.models import (AuditLog, BacktestRun, DatasetManifest, DatasetRow, DecisionMemory, FeatureDefinition, FeatureSnapshot, MarketSnapshot, ModelInferenceLog, ModelVersion, RawMarketBar, RawMarketTick, RegimeHistory,
                             ShadowDecision, ShadowOutcome, SystemControlState, TradeMemory)
from database.session import check_database
from models.registry import ModelRegistry
from monitoring.alerts import AlertService
from monitoring.control import EmergencyStopService
from sqlalchemy import func, select
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
        with self.sessions() as session:
            candidate = session.scalar(select(ModelVersion).where(ModelVersion.stage == "CANDIDATE").order_by(ModelVersion.id.desc()))
            shadow_model = session.scalar(select(ShadowDecision.model_version).order_by(ShadowDecision.decision_at.desc()))
        equity, high_water = float(account.get("equity", 0) or 0), float(account.get("high_water_equity", account.get("equity", 0)) or 0)
        return {"mt5_status": self.health().get("mt5"), "environment": self.settings.app_env.value, "mode": self.settings.trading_mode.value,
                "active_model": production["model_id"] if production else None,
                "candidate_model": candidate.version if candidate else None, "candidate_status": candidate.stage if candidate else None,
                "shadow_model": shadow_model, "balance": account.get("balance"), "equity": account.get("equity"),
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
        candidate = candidates[-1] if candidates else None
        candidate_payload = None if candidate is None else json.loads(candidate.metadata_json)
        artifact_ok = bool(candidate and candidate_payload.get("artifact_path") and Path(candidate_payload["artifact_path"]).is_file())
        return {"active_brain": production["model_id"] if production else None, "model_status": production["status"] if production else None,
                "current_candidate": candidate.version if candidate else None, "current_candidate_status": candidate.stage if candidate else None,
                "ai_health": "AVAILABLE" if artifact_ok else "UNAVAILABLE",
                "latest_decision": None if not decision else {"direction": decision.direction, "confidence": decision.confidence,
                    "model_version": decision.model_version, "feature_version": decision.feature_version,
                    "regime": snapshot.regime if snapshot else None, "timestamp": decision.timestamp},
                "last_inference": inference.timestamp if inference else None,
                "candidates": [{"model_id": item.version, "status": item.stage, "historical": item is not candidate,
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
        return {"production_champion": self.registry.production(), "current_candidate": models[-1] if models else None,
            "models": models, "supported_research_universe": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "ETHUSD"],
            "unavailable_on_current_broker": {}}

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
            "supported_research_universe": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "ETHUSD"],
            "unavailable_on_current_broker": {},
            "timeframes": sorted({item["timeframe"] for item in coverage}), "aggregate": aggregate, "coverage": coverage,
            "dataset": dataset, "dependency_audit": dependency_audit(definitions)}

    def backtest_runs(self) -> list[dict]:
        with self.sessions() as session: rows = session.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc())).all()
        return [{"run_id": row.run_id, "strategy_version": row.strategy_version, "symbol": row.symbol, "timeframe": row.timeframe,
            "configuration": row.configuration, "metrics": row.results.get("metrics", {}), "metadata": row.results.get("metadata", {}),
            "created_at": row.created_at} for row in rows]

    def live_shadow(self, limit: int = 100) -> dict:
        from collections import Counter
        with self.sessions() as session:
            model_version = session.scalar(select(ShadowDecision.model_version).order_by(ShadowDecision.decision_at.desc())) or "Brain-v3"
            registry = session.scalar(select(ModelVersion).where(ModelVersion.version == model_version))
            metadata = json.loads(registry.metadata_json) if registry else {}
            status_key = "brain_v3_btcusd_shadow_service" if model_version == "Brain-v3" else "brain_v2_shadow_service"
            state = session.get(SystemControlState, status_key)
            symbols = list(session.scalars(select(ShadowDecision.symbol).where(
                ShadowDecision.model_version == model_version).distinct()))
            if not symbols: symbols = ["BTCUSD"] if model_version == "Brain-v3" else ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
            rows = session.scalars(select(ShadowDecision).where(ShadowDecision.model_version == model_version)
                .order_by(ShadowDecision.decision_at.desc()).limit(max(1, min(limit, 500)))).all()
            all_rows = session.scalars(select(ShadowDecision).where(ShadowDecision.model_version == model_version)).all()
            outcomes = {item.decision_id: item for item in session.scalars(select(ShadowOutcome)).all()}
            paper_trades = session.scalar(select(func.count(TradeMemory.id)).join(DecisionMemory,
                TradeMemory.decision_id == DecisionMemory.decision_id).where(DecisionMemory.environment == "PAPER")) or 0
        resolved = set(outcomes)
        class_counts = Counter(item.final_decision for item in all_rows)
        risk_counts = Counter(item.risk_status for item in all_rows)
        per_symbol = {symbol: {"total": sum(item.symbol == symbol for item in all_rows),
            "LONG": sum(item.symbol == symbol and item.final_decision == "LONG" for item in all_rows),
            "SHORT": sum(item.symbol == symbol and item.final_decision == "SHORT" for item in all_rows),
            "NO_TRADE": sum(item.symbol == symbol and item.final_decision == "NO_TRADE" for item in all_rows),
            "observation_status": "OBSERVING" if any(item.symbol == symbol for item in all_rows) else "WAITING_FOR_NEXT_CLOSED_M5"}
            for symbol in symbols}
        service = dict(state.value) if state else {"status": "NOT_STARTED", "last_processed_closed_m5": {}}
        pid = service.get("observer_pid")
        alive = self._process_alive(pid) if pid else None
        service["process_alive"] = alive
        if service.get("status") in {"STOPPED", "ERROR"}:
            pass
        elif pid and not alive:
            service["status"] = "STOPPED"
            service["stop_reason"] = service.get("stop_reason") or "OBSERVER_PROCESS_EXITED"
        elif state and service.get("last_poll_at"):
            poll = datetime.fromisoformat(service["last_poll_at"])
            if poll.tzinfo is None: poll = poll.replace(tzinfo=timezone.utc)
            service["status"] = ("ACTIVE_WAITING_FOR_NEXT_CLOSED_M5" if alive else "UNKNOWN_PROCESS_WAITING_FOR_NEXT_CLOSED_M5") if (datetime.now(timezone.utc) - poll).total_seconds() <= 150 else "STALE_NO_RECENT_POLL"
        service.setdefault("stop_reason", None)
        return {"service": service, "environment": "SHADOW", "runtime_paper_trades": paper_trades,
            "live_permission": self.settings.allow_live_trading,
            "model_version": model_version, "model_status": registry.stage if registry else None,
            "artifact_hash": metadata.get("artifact_hash") or (all_rows[0].artifact_hash if all_rows else None),
            "counts": {"total": len(all_rows), **{label: class_counts[label] for label in ("LONG", "SHORT", "NO_TRADE")},
                "risk_pass": risk_counts["PASS"], "risk_block": risk_counts["BLOCK"],
                "orders_submitted": sum(item.order_submitted for item in all_rows)},
            "per_symbol": per_symbol,
            "outcomes": {"pending": sum(item.decision_id not in resolved for item in all_rows),
                "resolved": sum(item.decision_id in resolved for item in all_rows)},
            "decisions": [{"decision_id": item.decision_id, "decision_time": item.decision_at,
                "symbol": item.symbol, "timeframe": item.timeframe, "model_version": item.model_version,
                "artifact_hash": item.artifact_hash, "dataset_version": item.dataset_version,
                "dataset_hash": item.dataset_hash, "feature_set_version": item.feature_set_version,
                "feature_snapshot_id": item.feature_snapshot_id, "probabilities": item.probabilities,
                "raw_prediction": item.raw_prediction, "final_decision": item.final_decision,
                "confidence": item.confidence, "confidence_threshold": item.confidence_threshold,
                "market_context": item.market_context, "risk_status": item.risk_status,
                "risk_reason_codes": item.risk_reason_codes, "entry_reference": item.entry_reference,
                "proposed_stop": item.proposed_stop, "proposed_target": item.proposed_target,
                "environment": item.environment, "order_submitted": item.order_submitted,
                "outcome_status": "RESOLVED" if item.decision_id in resolved else "PENDING",
                "outcome": None if item.decision_id not in outcomes else {
                    "realized_label": outcomes[item.decision_id].realized_label,
                    "horizon_bar_at": outcomes[item.decision_id].horizon_bar_at,
                    "hypothetical_pnl_usd": outcomes[item.decision_id].hypothetical_pnl_usd}}
                for item in rows]}

    @staticmethod
    def _process_alive(pid: int) -> bool:
        try:
            if os.name == "nt":
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
                if not handle:
                    return False
                code = ctypes.c_ulong()
                try:
                    return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError):
            return False
