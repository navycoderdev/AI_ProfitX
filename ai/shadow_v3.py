"""Observation-only Brain-v3 BTCUSD service for genuine IC Markets candles."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ai.artifacts import ModelArtifactManager
from ai.brain_v1 import REQUIRED_FEATURES
from ai.shadow import ShadowService
from ai.types import ProposalAction
from core.context import TradingContext
from core.modes import TradingMode
from database.models import DatasetManifest, FeatureSnapshot, ModelVersion, ShadowDecision, SystemControlState
from risk.types import AccountRiskState, RiskRequest


MODEL_VERSION = "Brain-v3"
DATASET_VERSION = "research-dataset-v4"
DATASET_HASH = "8e0d2fa19312a7b85134f72b8f3728a034b8dde1fb737bf867817c071e57932c"
ARTIFACT_SHA256 = "569af0c4c81339449a0cfcd788dc63e0a5f8af045dcddb088dc0ead402e7fb2b"
SYMBOL = "BTCUSD"
THRESHOLD = .40
STATUS_KEY = "brain_v3_btcusd_shadow_service"
SOURCE = "MT5:ICMarketsSC-Demo"


def load_model(sessions, path=Path("models/artifacts/Brain-v3.json")):
    if sha256(path.read_bytes()).hexdigest() != ARTIFACT_SHA256:
        raise ValueError("Brain-v3 artifact hash mismatch.")
    model, metadata = ModelArtifactManager(path.parent).load(MODEL_VERSION)
    if (model.version != MODEL_VERSION or model.feature_names != tuple(sorted(REQUIRED_FEATURES)) or
        metadata.get("dataset_hash") != DATASET_HASH or metadata.get("dataset_version") != DATASET_VERSION or
        metadata.get("market_data_source") != SOURCE or
        metadata.get("confidence_policy") != {"version": "confidence-policy-v3", "threshold": THRESHOLD}):
        raise ValueError("Brain-v3 locked lineage mismatch.")
    with sessions() as session:
        manifest = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == DATASET_VERSION,
            DatasetManifest.content_hash == DATASET_HASH, DatasetManifest.state == "FROZEN"))
        registry = session.scalar(select(ModelVersion).where(ModelVersion.version == MODEL_VERSION))
    if manifest is None or registry is None or registry.stage != "CANDIDATE":
        raise ValueError("Frozen Dataset v4 and CANDIDATE Brain-v3 are required.")
    return model


class BrainV3BTCShadowService(ShadowService):
    def __init__(self, sessions, gateway, settings, *, clock=None, risk_engine=None):
        if settings.allow_live_trading or settings.trading_mode is not TradingMode.SHADOW:
            raise ValueError("Brain-v3 Shadow requires SHADOW and LIVE disabled.")
        self.sessions, self.gateway, self.settings = sessions, gateway, settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.model = load_model(sessions)
        from data.normalizer import DataNormalizer
        from features.engine import FeatureEngine
        from risk.engine import DeterministicRiskEngine
        from risk.types import RiskProfile
        self.normalizer, self.features = DataNormalizer(), FeatureEngine()
        self.risk = risk_engine or DeterministicRiskEngine(RiskProfile())
        now = self.clock()
        with sessions() as session:
            state = session.get(SystemControlState, STATUS_KEY)
            if state is None:
                state = SystemControlState(key=STATUS_KEY, value={"status": "OBSERVING", "started_at": now.isoformat(),
                    "model_version": MODEL_VERSION, "artifact_hash": ARTIFACT_SHA256, "dataset_hash": DATASET_HASH,
                    "broker_server": "ICMarketsSC-Demo", "orders_submitted": 0})
                session.add(state); session.commit()
            self.started_at = datetime.fromisoformat(state.value["started_at"])

    def _risk(self, symbol, action, bar, values, now):
        if action is ProposalAction.NO_TRADE: return "BLOCK", ["NO_TRADE"]
        if bar.spread is None: return "BLOCK", ["RISK_INPUT_UNAVAILABLE_SPREAD"]
        account = self.gateway.account_snapshot()
        if not account or any(account.get(key) is None for key in ("balance", "equity", "margin_free")):
            return "BLOCK", ["RISK_INPUT_UNAVAILABLE_ACCOUNT"]
        context = TradingContext(None, symbol, "M5", "UNKNOWN", MODEL_VERSION, "shadow-observation-v3",
            self.risk.profile.name, self.settings.app_env, TradingMode.SHADOW)
        state = AccountRiskState(float(account["balance"]), float(account["equity"]), float(account["equity"]), float(account["margin_free"]))
        request = RiskRequest(symbol, action.value, bar.close, None, None, None, .01, bar.spread, 1.0,
            session=str(values.get("session", "UNKNOWN")))
        result = self.risk.evaluate(context, request, state, now=now)
        return ("PASS" if result.approved else "BLOCK"), [reason.value for reason in result.reason_codes]

    def _record(self, symbol, bar, values, numeric, context_times, now):
        decision_at = bar.timestamp + timedelta(minutes=5)
        probabilities = self.model.probabilities(numeric); raw = max(probabilities, key=probabilities.get)
        confidence = probabilities[raw]
        final = ProposalAction.NO_TRADE if raw is not ProposalAction.NO_TRADE and confidence < THRESHOLD else raw
        risk_status, reasons = self._risk(symbol, final, bar, values, now)
        key = sha256(f"{MODEL_VERSION}|{symbol}|{decision_at.isoformat()}|{SOURCE}".encode()).hexdigest()
        snapshot_id = str(uuid4())
        with self.sessions() as session:
            if session.scalar(select(ShadowDecision.id).where(ShadowDecision.decision_id == key)): return False
            session.add(FeatureSnapshot(snapshot_id=snapshot_id, symbol=symbol, timeframe="M5", decision_at=decision_at,
                feature_version="feature-set-v1", raw_data_cutoff=bar.timestamp, values=values,
                context={"source": SOURCE, "latest_fully_closed_context": context_times}))
            session.add(ShadowDecision(decision_id=key, decision_at=decision_at, symbol=symbol, timeframe="M5",
                model_version=MODEL_VERSION, artifact_hash=ARTIFACT_SHA256, dataset_version=DATASET_VERSION,
                dataset_hash=DATASET_HASH, feature_set_version="feature-set-v1", feature_snapshot_id=snapshot_id,
                raw_data_cutoff=bar.timestamp, probabilities={label.value: probabilities[label] for label in ProposalAction},
                raw_prediction=raw.value, final_decision=final.value, confidence=confidence,
                confidence_threshold=THRESHOLD, market_context={"source": SOURCE, "session": values.get("session"),
                "spread_points": bar.spread, "latest_fully_closed_context": context_times},
                risk_status=risk_status, risk_reason_codes=reasons, entry_reference=bar.close,
                proposed_stop=None, proposed_target=None, environment="SHADOW", order_submitted=False))
            try: session.commit()
            except IntegrityError: session.rollback(); return False
        return True

    def poll_once(self):
        now = self.clock(); bars = self._bars(SYMBOL, "M5", now)
        contexts = {tf: self._bars(SYMBOL, tf, now) for tf in ("M15", "M30", "H1", "H4")}
        with self.sessions() as session:
            latest = session.scalar(select(func.max(ShadowDecision.decision_at)).where(
                ShadowDecision.model_version == MODEL_VERSION, ShadowDecision.symbol == SYMBOL))
        lower = max(self.started_at, latest.replace(tzinfo=timezone.utc) if latest and latest.tzinfo is None else latest or self.started_at)
        created = Counter()
        for index, bar in enumerate(bars):
            if bar.timestamp + timedelta(minutes=5) <= lower: continue
            computed = self._values(SYMBOL, bars, contexts, index)
            if computed and self._record(SYMBOL, bar, *computed, now): created[SYMBOL] += 1
        self._resolve(SYMBOL, bars, now)
        with self.sessions() as session:
            state = session.get(SystemControlState, STATUS_KEY)
            state.value = {**state.value, "status": "OBSERVING", "last_poll_at": now.isoformat(),
                "last_processed_closed_m5": self._latest(SYMBOL), "orders_submitted": 0}
            session.commit()
        return {"at": now.isoformat(), "created": dict(created)}

    def _latest(self, symbol):
        with self.sessions() as session:
            at = session.scalar(select(func.max(ShadowDecision.decision_at)).where(
                ShadowDecision.model_version == MODEL_VERSION, ShadowDecision.symbol == symbol))
        return at.isoformat() if at else None
