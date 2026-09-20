"""Read-only forward Brain-v2 observation of genuine closed MT5 candles."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ai.brain_v1 import _numeric
from ai.types import ProposalAction
from config.settings import Settings
from core.context import TradingContext
from core.modes import TradingMode
from data.candle_builder import TIMEFRAME_SECONDS
from data.normalizer import DataNormalizer
from database.models import DatasetManifest, FeatureSnapshot, ModelVersion, ShadowDecision, ShadowOutcome, SystemControlState
from features.engine import FeatureEngine
from risk.engine import DeterministicRiskEngine
from risk.types import AccountRiskState, RiskProfile, RiskRequest


MODEL_VERSION = "Brain-v2"
ARTIFACT_SHA256 = "ccf4a1aa2e12496b3eac099551189f3a6d0e6321cf5134e59bb052aa6dc2b71d"
DATASET_VERSION = "research-dataset-v3"
DATASET_HASH = "790d8e18de1f8d8fa9e159a26b2cf8628fc813aa2a094b6909e84849f15f7de4"
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD")
CONTEXTS = ("M15", "M30", "H1", "H4")
THRESHOLD = .40
STATUS_KEY = "brain_v2_shadow_service"


def load_locked_model(sessions, path: Path = Path("models/artifacts/Brain-v2.json")):
    from ai.artifacts import ModelArtifactManager
    from ai.brain_v1 import REQUIRED_FEATURES

    if sha256(path.read_bytes()).hexdigest() != ARTIFACT_SHA256:
        raise ValueError("Brain-v2 artifact hash mismatch; Shadow fails closed.")
    model, metadata = ModelArtifactManager(path.parent).load(MODEL_VERSION)
    expected_names = tuple(sorted(REQUIRED_FEATURES))
    if (model.version != MODEL_VERSION or model.feature_names != expected_names or len(model.feature_names) != 22
        or len(model.means) != 22 or len(model.scales) != 22 or len(model.weights) != 3
        or any(len(weights) != 23 for weights in model.weights)
        or metadata.get("dataset_version") != DATASET_VERSION or metadata.get("dataset_hash") != DATASET_HASH
        or metadata.get("feature_set_version") != "feature-set-v1"
        or metadata.get("confidence_policy") != {"version": "confidence-policy-v2", "threshold": THRESHOLD}):
        raise ValueError("Brain-v2 schema, ordering, or lineage mismatch; Shadow fails closed.")
    with sessions() as session:
        manifest = session.scalar(select(DatasetManifest).where(DatasetManifest.dataset_version == DATASET_VERSION,
            DatasetManifest.content_hash == DATASET_HASH, DatasetManifest.state == "FROZEN"))
        registry = session.scalar(select(ModelVersion).where(ModelVersion.version == MODEL_VERSION))
    if manifest is None or registry is None or registry.stage != "CANDIDATE":
        raise ValueError("Frozen dataset and CANDIDATE registry lineage are required for Shadow.")
    return model


class ShadowService:
    """Consumes only a read-only market gateway. No execution dependency exists."""
    def __init__(self, sessions, gateway, settings: Settings, *, clock=None, risk_engine=None,
                 artifact_path: Path = Path("models/artifacts/Brain-v2.json")):
        if settings.allow_live_trading or settings.trading_mode is not TradingMode.SHADOW:
            raise ValueError("Shadow requires SHADOW mode and LIVE permission false.")
        self.sessions, self.gateway, self.settings = sessions, gateway, settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.model = load_locked_model(sessions, artifact_path)
        self.normalizer, self.features = DataNormalizer(), FeatureEngine()
        self.risk = risk_engine or DeterministicRiskEngine(RiskProfile())
        with sessions() as session:
            state = session.get(SystemControlState, STATUS_KEY)
            now = self.clock()
            if state is None:
                state = SystemControlState(key=STATUS_KEY, value={"status": "OBSERVING", "started_at": now.isoformat(),
                    "model_version": MODEL_VERSION, "artifact_hash": ARTIFACT_SHA256, "orders_submitted": 0})
                session.add(state)
                session.commit()
            self.started_at = datetime.fromisoformat(state.value["started_at"])

    def _bars(self, symbol, timeframe, now):
        start = now - timedelta(days=14 if timeframe == "H4" else 7)
        payloads = self.gateway.candles(symbol, timeframe, start, now)
        unique = {}
        for payload in payloads:
            bar = self.normalizer.bar("MT5", symbol, timeframe, payload)
            if (bar.low <= 0 or bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close)):
                raise ValueError(f"Invalid broker OHLC for {symbol} {timeframe}; Shadow fails closed.")
            if bar.timestamp in unique and unique[bar.timestamp] != bar:
                raise ValueError(f"Conflicting broker candles for {symbol} {timeframe}; Shadow fails closed.")
            unique[bar.timestamp] = bar
        return sorted((bar for bar in unique.values() if bar.timestamp + timedelta(seconds=TIMEFRAME_SECONDS[timeframe]) <= now),
            key=lambda bar: bar.timestamp)

    def _values(self, symbol, bars, contexts, index):
        base = bars[index]
        decision_at = base.timestamp + timedelta(minutes=5)
        history = bars[max(0, index - 99):index + 1]
        if len(history) < 51:
            return None
        closed = {tf: [bar for bar in context if bar.timestamp + timedelta(seconds=TIMEFRAME_SECONDS[tf]) <= decision_at]
            for tf, context in contexts.items()}
        if any(len(items) < 2 for items in closed.values()):
            return None
        values = self.features.calculate(history, base.timestamp, closed["M15"])
        for tf, items in closed.items():
            values[f"{tf.lower()}_trend"] = "UP" if items[-1].close > items[-2].close else "DOWN" if items[-1].close < items[-2].close else "FLAT"
        values["feature_version"] = "feature-set-v1"
        numeric = _numeric(values)
        if tuple(sorted(numeric)) != self.model.feature_names:
            raise ValueError("Forward feature schema/order mismatch; Shadow fails closed.")
        return values, numeric, {tf: items[-1].timestamp.isoformat() for tf, items in closed.items()}

    def _risk(self, symbol, action, bar, values, now):
        if action is ProposalAction.NO_TRADE:
            return "BLOCK", ["NO_TRADE"]
        if bar.spread is None:
            return "BLOCK", ["RISK_INPUT_UNAVAILABLE_SPREAD"]
        account = self.gateway.account_snapshot()
        if not account or any(account.get(key) is None for key in ("balance", "equity", "margin_free")):
            return "BLOCK", ["RISK_INPUT_UNAVAILABLE_ACCOUNT"]
        point = 0.01 if symbol == "XAUUSD" else .001 if symbol == "USDJPY" else .00001
        context = TradingContext(None, symbol, "M5", "UNKNOWN", MODEL_VERSION, "shadow-observation-v1",
            self.risk.profile.name, self.settings.app_env, TradingMode.SHADOW)
        state = AccountRiskState(float(account["balance"]), float(account["equity"]), float(account["equity"]),
            float(account["margin_free"]))
        # No approved strategy supplies a hard stop/target yet. Preserve nulls; the independent
        # Risk Engine rejects the hypothetical proposal with INVALID_STOP.
        request = RiskRequest(symbol, action.value, bar.close, None, None, None, point, bar.spread, 1.0,
            session=str(values.get("session", "UNKNOWN")))
        result = self.risk.evaluate(context, request, state, now=now)
        return ("PASS" if result.approved else "BLOCK"), [reason.value for reason in result.reason_codes]

    def _record(self, symbol, bar, values, numeric, context_times, now):
        decision_at = bar.timestamp + timedelta(minutes=5)
        probabilities = self.model.probabilities(numeric)
        raw = max(probabilities, key=probabilities.get)
        confidence = probabilities[raw]
        final = ProposalAction.NO_TRADE if raw is not ProposalAction.NO_TRADE and confidence < THRESHOLD else raw
        risk_status, reasons = self._risk(symbol, final, bar, values, now)
        key = sha256(f"{MODEL_VERSION}|{symbol}|{decision_at.isoformat()}".encode()).hexdigest()
        snapshot_id = str(uuid4())
        with self.sessions() as session:
            if session.scalar(select(ShadowDecision.id).where(ShadowDecision.decision_id == key)):
                return False
            session.add(FeatureSnapshot(snapshot_id=snapshot_id, symbol=symbol, timeframe="M5", decision_at=decision_at,
                feature_version="feature-set-v1", raw_data_cutoff=bar.timestamp, values=values,
                context={"source": "MT5_FORWARD_SHADOW", "latest_fully_closed_context": context_times}))
            session.add(ShadowDecision(decision_id=key, decision_at=decision_at, symbol=symbol, timeframe="M5",
                model_version=MODEL_VERSION, artifact_hash=ARTIFACT_SHA256, dataset_version=DATASET_VERSION,
                dataset_hash=DATASET_HASH, feature_set_version="feature-set-v1", feature_snapshot_id=snapshot_id,
                raw_data_cutoff=bar.timestamp, probabilities={label.value: probabilities[label] for label in ProposalAction},
                raw_prediction=raw.value, final_decision=final.value, confidence=confidence, confidence_threshold=THRESHOLD,
                market_context={"session": values.get("session"), "spread_points": bar.spread,
                    "latest_fully_closed_context": context_times}, risk_status=risk_status, risk_reason_codes=reasons,
                entry_reference=bar.close, proposed_stop=None, proposed_target=None, environment="SHADOW",
                order_submitted=False))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
        return True

    def _resolve(self, symbol, bars, now):
        with self.sessions() as session:
            pending = session.scalars(select(ShadowDecision).outerjoin(ShadowOutcome,
                ShadowOutcome.decision_id == ShadowDecision.decision_id).where(ShadowDecision.symbol == symbol,
                ShadowOutcome.id.is_(None))).all()
            for decision in pending:
                cutoff = decision.raw_data_cutoff.replace(tzinfo=timezone.utc) if decision.raw_data_cutoff.tzinfo is None else decision.raw_data_cutoff
                available = bars
                if bars and cutoff < bars[0].timestamp:
                    payloads = self.gateway.candles(symbol, "M5", cutoff, now)
                    available = sorted((self.normalizer.bar("MT5", symbol, "M5", payload) for payload in payloads),
                        key=lambda item: item.timestamp)
                future = [bar for bar in available if bar.timestamp > cutoff and
                    bar.timestamp + timedelta(minutes=5) <= now]
                if len({bar.timestamp for bar in future}) != len(future) or any(
                    bar.low <= 0 or bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close)
                    for bar in future):
                    raise ValueError("Invalid or duplicate future M5 bars; Shadow outcome fails closed.")
                if len(future) < 12:
                    continue
                horizon = future[11]
                if horizon.timestamp <= cutoff:
                    raise ValueError("Shadow horizon leakage detected.")
                move = (horizon.close - decision.entry_reference) / decision.entry_reference
                label = "LONG" if move >= .0005 else "SHORT" if move <= -.0005 else "NO_TRADE"
                session.add(ShadowOutcome(decision_id=decision.decision_id, resolved_at=now,
                    horizon_bar_at=horizon.timestamp, entry_close=decision.entry_reference,
                    horizon_close=horizon.close, realized_label=label, hypothetical_pnl_usd=None))
            session.commit()

    def poll_once(self) -> dict:
        now = self.clock()
        created = Counter()
        for symbol in SYMBOLS:
            bars = self._bars(symbol, "M5", now)
            contexts = {tf: self._bars(symbol, tf, now) for tf in CONTEXTS}
            with self.sessions() as session:
                latest = session.scalar(select(func.max(ShadowDecision.decision_at)).where(
                    ShadowDecision.model_version == MODEL_VERSION, ShadowDecision.symbol == symbol))
            lower = max(self.started_at, latest.replace(tzinfo=timezone.utc) if latest and latest.tzinfo is None else latest or self.started_at)
            for index, bar in enumerate(bars):
                decision_at = bar.timestamp + timedelta(minutes=5)
                if decision_at <= lower:
                    continue
                computed = self._values(symbol, bars, contexts, index)
                if computed and self._record(symbol, bar, *computed, now):
                    created[symbol] += 1
            self._resolve(symbol, bars, now)
        with self.sessions() as session:
            state = session.get(SystemControlState, STATUS_KEY)
            state.value = {**state.value, "status": "OBSERVING", "last_poll_at": now.isoformat(),
                "last_processed_closed_m5": {symbol: self._latest(symbol) for symbol in SYMBOLS},
                "orders_submitted": 0}
            session.commit()
        return {"at": now.isoformat(), "created": dict(created)}

    def _latest(self, symbol):
        with self.sessions() as session:
            at = session.scalar(select(func.max(ShadowDecision.decision_at)).where(
                ShadowDecision.model_version == MODEL_VERSION, ShadowDecision.symbol == symbol))
        return at.isoformat() if at else None
