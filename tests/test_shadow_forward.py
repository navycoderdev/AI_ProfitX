import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ai import shadow
from ai.brain_v1 import REQUIRED_FEATURES
from ai.types import ProposalAction
from config.settings import Settings
from database.models import ShadowDecision, ShadowOutcome, TradeMemory
from database.session import initialize_database
from risk.types import RiskDecision, RiskProfile, RiskReasonCode, RiskStatus


class FixedModel:
    version = "Brain-v2"
    feature_names = tuple(sorted(REQUIRED_FEATURES))

    def __init__(self, action):
        self.action = action

    def probabilities(self, features):
        return {label: (.8 if label is self.action else .1) for label in ProposalAction}


class FakeGateway:
    def __init__(self, start):
        self.start = start
        self.orders = 0

    def candles(self, symbol, timeframe, start, end):
        minutes = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}[timeframe]
        first = self.start - timedelta(days=2)
        count = int((end - first).total_seconds() // (minutes * 60)) + 1
        return [{"time": int((first + timedelta(minutes=minutes * i)).timestamp()),
            "open": 1.1 + i * .00001, "high": 1.101 + i * .00001,
            "low": 1.099 + i * .00001, "close": 1.1 + i * .00001,
            "tick_volume": 10, "spread": 2} for i in range(count)]

    def account_snapshot(self):
        return {"balance": 10000, "equity": 10000, "margin_free": 10000}

    def submit_order(self, *args, **kwargs):
        self.orders += 1
        raise AssertionError("Shadow must never call execution gateway")


class PassingRisk:
    profile = RiskProfile()

    def evaluate(self, context, request, state, now=None):
        return RiskDecision(RiskStatus.APPROVED, (RiskReasonCode.APPROVED,), .01)


def setup(tmp_path, monkeypatch, action=ProposalAction.NO_TRADE, risk=None):
    start = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
    now = [start + timedelta(minutes=2)]
    sessions = initialize_database(Settings(_env_file=None, trading_mode="SHADOW", database_url=f"sqlite:///{tmp_path / 'shadow.db'}"))
    gateway = FakeGateway(start)
    monkeypatch.setattr(shadow, "load_locked_model", lambda *args, **kwargs: FixedModel(action))
    settings = Settings(_env_file=None, trading_mode="SHADOW", database_url=f"sqlite:///{tmp_path / 'shadow.db'}")
    service = shadow.ShadowService(sessions, gateway, settings, clock=lambda: now[0], risk_engine=risk)
    return service, sessions, gateway, now, start, settings


def test_forming_m5_no_decision_then_closed_no_trade_persists_restart(tmp_path, monkeypatch):
    service, sessions, gateway, now, start, settings = setup(tmp_path, monkeypatch)
    assert service.poll_once()["created"] == {}
    now[0] = start + timedelta(minutes=5, seconds=1)
    service.poll_once()
    with sessions() as session:
        rows = session.scalars(select(ShadowDecision)).all()
        assert len(rows) == 4
        assert all(row.final_decision == "NO_TRADE" and row.risk_status == "BLOCK" and not row.order_submitted for row in rows)
    restarted = shadow.ShadowService(sessions, gateway, settings, clock=lambda: now[0])
    assert restarted.poll_once()["created"] == {}
    with sessions() as session:
        assert session.scalar(select(func.count(ShadowDecision.id))) == 4
        assert session.scalar(select(func.count(TradeMemory.id))) == 0
    assert gateway.orders == 0
    assert settings.allow_live_trading is False


def test_forming_higher_timeframe_excluded_and_feature_order_fails_closed(tmp_path, monkeypatch):
    service, sessions, gateway, now, start, settings = setup(tmp_path, monkeypatch)
    now[0] = start + timedelta(minutes=5, seconds=1)
    bars = service._bars("EURUSD", "M5", now[0])
    contexts = {tf: service._bars("EURUSD", tf, now[0]) for tf in shadow.CONTEXTS}
    values, numeric, times = service._values("EURUSD", bars, contexts, len(bars)-1)
    for tf, at in times.items():
        assert datetime.fromisoformat(at) + timedelta(seconds=shadow.TIMEFRAME_SECONDS[tf]) <= now[0]
    service.model.feature_names = tuple(reversed(service.model.feature_names))
    with pytest.raises(ValueError, match="schema/order"):
        service._values("EURUSD", bars, contexts, len(bars)-1)


def test_risk_pass_still_zero_orders_and_horizon_waits_for_twelve_closed_bars(tmp_path, monkeypatch):
    service, sessions, gateway, now, start, _ = setup(tmp_path, monkeypatch, ProposalAction.LONG, PassingRisk())
    now[0] = start + timedelta(minutes=5, seconds=1)
    service.poll_once()
    with sessions() as session:
        first_id = session.scalar(select(ShadowDecision.decision_id).where(ShadowDecision.symbol == "XAUUSD"))
        assert session.scalar(select(ShadowDecision).where(ShadowDecision.decision_id == first_id)).risk_status == "PASS"
    now[0] = start + timedelta(minutes=60, seconds=1)
    service.poll_once()
    with sessions() as session:
        assert session.scalar(select(ShadowOutcome).where(ShadowOutcome.decision_id == first_id)) is None
    now[0] = start + timedelta(minutes=65, seconds=1)
    service.poll_once()
    with sessions() as session:
        assert session.scalar(select(ShadowOutcome).where(ShadowOutcome.decision_id == first_id)) is not None
        assert session.scalar(select(func.count(TradeMemory.id))) == 0
    assert gateway.orders == 0


def test_artifact_hash_mismatch_fails_before_any_decision(tmp_path):
    path = tmp_path / "Brain-v2.json"
    path.write_text('{"model": "tampered"}', encoding="utf-8")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        shadow.load_locked_model(None, path)


def test_artifact_feature_order_and_policy_mismatch_fail_closed(tmp_path, monkeypatch):
    original = json.loads(Path("models/artifacts/Brain-v2.json").read_text(encoding="utf-8"))
    for field in ("order", "policy"):
        payload = json.loads(json.dumps(original))
        if field == "order":
            payload["model"]["feature_names"].reverse()
        else:
            payload["metadata"]["confidence_policy"]["threshold"] = .45
        path = tmp_path / "Brain-v2.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(shadow, "ARTIFACT_SHA256", sha256(path.read_bytes()).hexdigest())
        with pytest.raises(ValueError, match="schema, ordering, or lineage"):
            shadow.load_locked_model(None, path)
