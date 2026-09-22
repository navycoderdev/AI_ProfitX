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
        observations = []
        if self.live_market_source and self.health().get("mt5"):
            observations = self.live_market_source()
        with self.sessions() as session:
            ticks = list(session.scalars(select(RawMarketTick).order_by(RawMarketTick.id.desc())))
            regimes = list(session.scalars(select(RegimeHistory).order_by(RegimeHistory.id.desc())))
            features = list(session.scalars(select(FeatureSnapshot).order_by(FeatureSnapshot.decision_at.desc())))
        latest_ticks, latest_regimes = {}, {}
        latest_features = {}
        for item in ticks: latest_ticks.setdefault(item.symbol, item)
        for item in regimes: latest_regimes.setdefault(item.symbol, item)
        for item in features: latest_features.setdefault(item.symbol, item)
        if observations:
            for item in observations:
                context = latest_regimes.get(item["symbol"]); feature = latest_features.get(item["symbol"])
                item["timeframe"] = context.timeframe if context else feature.timeframe if feature else None
                item["regime"] = context.label if context else (feature.values or {}).get("session", "UNAVAILABLE") if feature else "UNAVAILABLE"
                item["volatility"] = ((context.measurements or {}).get("volatility_percentile") if context else
                    (feature.values or {}).get("volatility_percentile") if feature else None)
            return observations
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
            shadow = session.scalar(select(ShadowDecision).where(ShadowDecision.model_version == "Brain-v3")
                .order_by(ShadowDecision.decision_at.desc()))
            manifest = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == "research-dataset-v4",
                DatasetManifest.state == "FROZEN").order_by(DatasetManifest.frozen_at.desc()))
        production = self.registry.production()
        candidate = candidates[-1] if candidates else None
        candidate_payload = None if candidate is None else json.loads(candidate.metadata_json)
        artifact_ok = bool(candidate and candidate_payload.get("artifact_path") and Path(candidate_payload["artifact_path"]).is_file())
        return {"active_brain": production["model_id"] if production else None, "model_status": production["status"] if production else None,
                "current_candidate": candidate.version if candidate else None, "current_candidate_status": candidate.stage if candidate else None,
                "ai_health": "AVAILABLE" if artifact_ok else "UNAVAILABLE",
                "dataset": None if not manifest else {"version": manifest.dataset_version, "hash": manifest.content_hash,
                    "state": manifest.state},
                "latest_decision": ({"direction": shadow.final_decision, "confidence": shadow.confidence,
                    "model_version": shadow.model_version, "feature_version": shadow.feature_set_version,
                    "regime": (shadow.market_context or {}).get("session"), "timestamp": shadow.decision_at}
                    if shadow else None if not decision else {"direction": decision.direction, "confidence": decision.confidence,
                    "model_version": decision.model_version, "feature_version": decision.feature_version,
                    "regime": snapshot.regime if snapshot else None, "timestamp": decision.timestamp}),
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
        current_candidate = next((item for item in reversed(models) if item["status"] == "CANDIDATE"), None)
        brain4_path = Path("reports/brain_v4_oos_evaluation.json")
        if brain4_path.is_file():
            evidence = json.loads(brain4_path.read_text(encoding="utf-8")); overall = evidence["results"]["ALL"]
            per_symbol = {symbol: {"coverage_status": "AVAILABLE", "prediction_count": value["predictions"],
                "prediction_counts": value["prediction_counts"], "simulated_trade_count": value["trades"],
                "wins": value["wins"], "losses": value["losses"], "net_pnl_usd": value["net_pnl_usd"],
                "profit_factor": value["profit_factor"], "max_drawdown": value["max_drawdown"]}
                for symbol, value in evidence["results"].items() if symbol in {"EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "ETHUSD"}}
            simulation = {"run_id": evidence["frozen_config_hash"][:12], "simulation_timestamp": datetime.fromtimestamp(
                brain4_path.stat().st_mtime, tz=timezone.utc), "dataset_version": "research-dataset-v4",
                "dataset_hash": evidence["dataset_hash"], "prediction_count": overall["predictions"],
                "prediction_counts": overall["prediction_counts"], "simulated_trade_count": overall["trades"],
                "wins": overall["wins"], "losses": overall["losses"], "gross_pnl_usd": overall["gross_pnl_usd"],
                "transaction_costs_usd": overall["costs_usd"], "slippage_usd": overall["slippage_usd"],
                "net_pnl_usd": overall["net_pnl_usd"], "profit_factor": overall["profit_factor"],
                "max_drawdown": overall["max_drawdown"], "runtime_paper_trades": 0, "per_symbol": per_symbol}
            models.append({"model_id": "Brain-v4", "status": "RESEARCH_ONLY", "parent_model": None,
                "feature_version": "brain-v4-research-features-v1", "confidence_policy": {"threshold": evidence["confidence_threshold"]},
                "validation_metrics": None, "out_of_sample_metrics": None, "oos_simulation": simulation,
                "frozen_config_hash": evidence["frozen_config_hash"], "go_no_go": evidence["go_no_go"]})
        return {"production_champion": self.registry.production(), "current_candidate": current_candidate,
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
            shadow = list(session.scalars(select(ShadowDecision).where(ShadowDecision.model_version == "Brain-v3")))
        from collections import Counter
        reasons = Counter(code for item in shadow for code in item.risk_reason_codes)
        last = max(shadow, key=lambda item: item.decision_at) if shadow else None
        frozen = json.loads(Path("reports/brain_v4_pre_oos_config.json").read_text(encoding="utf-8")) if Path("reports/brain_v4_pre_oos_config.json").is_file() else None
        return {"emergency_stop": self.emergency.status(), "current_policy": {"name": "conservative", "version": "risk-policy-v1",
                    "scope": "Brain-v3 SHADOW observational", "minimum_risk_reward": 1.0, "maximum_spread_points": 30},
                "brain_v4_research_profiles": None if not frozen else {"status": "RESEARCH_ONLY", "config_hash": frozen.get("content_hash"),
                    "profiles": frozen.get("stop_target_and_risk_policies")},
                "shadow_summary": {"total": len(shadow), "PASS": sum(x.risk_status == "PASS" for x in shadow),
                    "BLOCK": sum(x.risk_status == "BLOCK" for x in shadow), "reason_counts": dict(reasons),
                    "last_evaluation": last.decision_at if last else None},
                "risk_events": [{"action": item.action, "payload": item.payload, "timestamp": item.created_at} for item in events],
                "alerts": [item for item in self.alerts.recent(100) if item["code"] in {"ABNORMAL_SPREAD", "STATE_MISMATCH", "MT5_DISCONNECTED"}]}
    def system_health(self) -> dict:
        health = self.health(); last_tick = None; quote_count = 0; expected_quotes = 6
        with self.sessions() as session:
            tick = session.scalar(select(RawMarketTick).order_by(RawMarketTick.id.desc()))
            candidate = session.scalar(select(ModelVersion).where(ModelVersion.stage == "CANDIDATE").order_by(ModelVersion.id.desc()))
            inference = session.scalar(select(ModelInferenceLog).order_by(ModelInferenceLog.id.desc()))
            shadow_state = session.get(SystemControlState, "brain_v3_btcusd_shadow_service")
        if tick: last_tick = tick.timestamp
        # PAPER/SHADOW can read directly from MT5 before a background collector is started.
        # Treat a current terminal quote as a healthy feed; do not claim a stale database tick instead.
        if health.get("mt5") and self.live_market_source:
            observations = self.live_market_source()
            quote_count = sum(bool(item.get("quote_available", item.get("bid") is not None and item.get("ask") is not None)) for item in observations)
            timestamps = [item.get("timestamp") for item in observations if item.get("timestamp")]
            if timestamps:
                last_tick = max(timestamps)
        if last_tick is not None and last_tick.tzinfo is None: last_tick = last_tick.replace(tzinfo=timezone.utc)
        stale = last_tick is None or (datetime.now(timezone.utc) - last_tick).total_seconds() > self.settings.data_feed_stale_seconds
        artifact_ok = False
        if candidate:
            metadata = json.loads(candidate.metadata_json); artifact_ok = bool(metadata.get("artifact_path") and Path(metadata["artifact_path"]).is_file())
        worker = (shadow_state.value or {}).get("status", "NOT_STARTED") if shadow_state else "NOT_STARTED"
        complete_feed = quote_count == expected_quotes
        return {**health, "status": "ok" if health.get("database") and health.get("mt5") and artifact_ok and not stale and complete_feed else "degraded",
                "last_successful_tick": last_tick, "data_feed_stale": stale, "ai_inference": artifact_ok,
                "market_quotes_available": quote_count, "market_quotes_expected": expected_quotes,
                "market_feed_status": "HEALTHY" if complete_feed and not stale else "DEGRADED",
                "risk_engine": True, "execution_engine": "DISABLED_SHADOW" if self.settings.trading_mode.value == "SHADOW" else "DISABLED",
                "background_workers": worker, "inference_latency": getattr(inference, "latency_ms", None) if inference else None,
                "execution_latency": None, "recent_alerts": self.alerts.recent(20)}
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
        raw_latest = max((item["last"] for item in coverage if item["last"]), default=None)
        return {"total_candles": sum(item["count"] for item in coverage), "symbols": sorted({item["symbol"] for item in coverage}),
            "supported_research_universe": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "ETHUSD"],
            "unavailable_on_current_broker": {},
            "timeframes": sorted({item["timeframe"] for item in coverage}), "aggregate": aggregate,
            "raw_data_latest": raw_latest, "quality_report_at": (aggregate or {}).get("created_at"), "coverage": coverage,
            "dataset": dataset, "dependency_audit": dependency_audit(definitions)}

    def backtest_runs(self) -> list[dict]:
        with self.sessions() as session: rows = session.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc())).all()
        result = [{"run_id": row.run_id, "strategy_version": row.strategy_version, "symbol": row.symbol, "timeframe": row.timeframe,
            "configuration": row.configuration, "metrics": row.results.get("metrics", {}), "metadata": row.results.get("metadata", {}),
            "created_at": row.created_at} for row in rows]
        for version in ("Brain-v3", "Brain-v4"):
            path = Path(f"reports/{version.lower().replace('-', '_')}_oos_{'simulation' if version == 'Brain-v3' else 'evaluation'}.json")
            if not path.is_file(): continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            scopes = payload.get("per_symbol") if version == "Brain-v3" else payload.get("results")
            overall = payload if version == "Brain-v3" else scopes["ALL"]
            result.insert(0, {"run_id": payload.get("run_id", payload.get("frozen_config_hash", "")[:12]),
                "strategy_version": version, "symbol": "ALL", "timeframe": "M5",
                "configuration": {"status": "CANDIDATE" if version == "Brain-v3" else "RESEARCH_ONLY"},
                "metrics": {"trade_count": overall.get("simulated_trade_count", overall.get("trades")),
                    "net_pnl": overall.get("net_pnl_usd"), "profit_factor": overall.get("profit_factor"),
                    "maximum_drawdown": overall.get("max_drawdown"),
                    "transaction_costs": overall.get("transaction_costs_usd", overall.get("costs_usd"))},
                "metadata": {"per_symbol": scopes, "evidence": str(path)}, "created_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)})
        return result

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
        observation_status = service.get("status", "NOT_STARTED")
        for values in per_symbol.values():
            values["observation_status"] = observation_status
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
