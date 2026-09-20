from datetime import datetime, timedelta, timezone

from backtesting.engine import BacktestEngine
from backtesting.types import BacktestConfig
from data.types import MarketBar
from models.registry import ModelRegistry
from models.types import ModelMetadata, ModelStatus
from validation.champion import ChampionChallengerEngine
from validation.promotion import PromotionGate, ShadowEvaluationEngine
from validation.statistics import MonteCarloAnalyzer
from validation.types import PromotionCriteria, ValidationResult
from validation.validators import CostStressTester, OutOfSampleValidator, WalkForwardValidator
from database.session import initialize_database
from config.settings import Settings
from strategies.baselines import BaselineSignal
from backtesting.types import Side


def bars(count=60):
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    return [MarketBar("MT5", "EURUSD", "M5", start + timedelta(minutes=5 * index), 1.1 + index * .0002,
                      1.1001 + index * .0002, 1.0999 + index * .0002, 1.1 + index * .0002, 1, 1, 10) for index in range(count)]


class OneTrade:
    version = "validation-strategy"
    def __init__(self): self.sent = False
    def evaluate(self, history):
        if not self.sent and len(history) > 2:
            self.sent = True; return BaselineSignal(Side.LONG, history[-1].close - .01, history[-1].close + .001)
        return BaselineSignal(None)


def valid_results():
    return [ValidationResult("out_of_sample", True, {"trade_count": 30, "net_pnl": 10}),
            ValidationResult("walk_forward", True, {"pass_rate": .8, "maximum_drawdown": .1}),
            ValidationResult("cost_stress", True, {"scenarios": {"2.0": {"net_pnl": 2}}}),
            ValidationResult("monte_carlo", True, {"terminal_pnl_p05": 1}),
            ValidationResult("regime_performance", True, {"coverage": 2})]


def register_candidate(registry, version="Brain-v1"):
    registry.register_training(ModelMetadata(version, None, {}, {"start": "a", "end": "b"}, "phase3-v1", "linear", {}))
    registry.transition(version, ModelStatus.CANDIDATE)


def test_walk_forward_cost_stress_and_bootstrap_work():
    source, config = bars(), BacktestConfig("EURUSD", "M5")
    report = BacktestEngine().run(source, OneTrade(), config)
    walk = WalkForwardValidator().validate(source, OneTrade(), config, windows=3)
    stress = CostStressTester().validate(source, OneTrade(), config)
    monte = MonteCarloAnalyzer().analyze(report, simulations=20, seed=2)
    assert walk.metrics["windows"] == 3
    assert "scenarios" in stress.metrics
    assert monte.metrics["simulations"] == 20
    assert OutOfSampleValidator().validate(report, minimum_trades=1).name == "out_of_sample"


def test_champion_challenger_uses_multiple_risk_measures():
    champion = {"out_of_sample_net_pnl": 10, "maximum_drawdown": .2, "walk_forward_pass_rate": .6, "cost_stress_net_pnl": 3}
    weak = {"out_of_sample_net_pnl": 20, "maximum_drawdown": .3, "walk_forward_pass_rate": .5, "cost_stress_net_pnl": 2}
    assert ChampionChallengerEngine().compare(champion, weak).passed is False


def test_promotion_gate_rejects_weak_candidate_and_requires_explicit_live_authorization(tmp_path):
    sessions = initialize_database(Settings(database_url=f"sqlite:///{tmp_path}/gate.db")); registry = ModelRegistry(sessions, frozenset({"reviewer"}))
    register_candidate(registry)
    criteria = PromotionCriteria(minimum_shadow_observations=2, allow_live_promotion=True)
    gate = PromotionGate(registry, criteria)
    weak = gate.advance_to_paper("Brain-v1", [ValidationResult("out_of_sample", False, {"trade_count": 1}, ("BAD",))])
    assert weak.approved is False and registry.get("Brain-v1")["status"] == "REJECTED"
    register_candidate(registry, "Brain-v2")
    passed = gate.advance_to_paper("Brain-v2", valid_results())
    assert passed.target_stage == "PAPER" and registry.get("Brain-v2")["status"] == "PAPER"
    gate.advance_to_shadow("Brain-v2")
    promoted = gate.promote_live("Brain-v2", 2, valid_results(), "reviewer")
    assert promoted.approved is True and registry.production()["model_id"] == "Brain-v2"


def test_shadow_engine_makes_hypothetical_decisions_without_execution_dependency():
    class FakeInference:
        def infer(self, *_args): return "hypothetical-proposal"
    shadow = ShadowEvaluationEngine(FakeInference())
    record = shadow.observe({"snapshot_id": "s1"}, "RANGING", {"equity": 1})
    assert record["proposal"] == "hypothetical-proposal" and shadow.observation_count == 1
