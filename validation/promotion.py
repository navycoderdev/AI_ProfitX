from dataclasses import asdict

from ai.inference import InferenceEngine
from models.registry import ModelRegistry
from models.types import ModelStatus
from validation.types import PromotionCriteria, PromotionDecision, ValidationResult


class PromotionGate:
    """Multi-measure gate. It can advance stages or reject; it never auto-promotes to LIVE."""
    def __init__(self, registry: ModelRegistry, criteria: PromotionCriteria) -> None: self.registry, self.criteria = registry, criteria
    def evaluate(self, results: list[ValidationResult]) -> PromotionDecision:
        by_name = {result.name: result for result in results}; failures = []
        for result in results:
            if not result.passed: failures.extend(f"{result.name}:{failure}" for failure in result.failures)
        oos = by_name.get("out_of_sample"); walk = by_name.get("walk_forward"); stress = by_name.get("cost_stress"); monte = by_name.get("monte_carlo"); regimes = by_name.get("regime_performance")
        if not oos: failures.append("MISSING_OUT_OF_SAMPLE_VALIDATION")
        elif int(oos.metrics.get("trade_count", 0) or 0) < self.criteria.minimum_out_of_sample_trades: failures.append("OOS_SAMPLE_SIZE_BELOW_GATE")
        if not walk: failures.append("MISSING_WALK_FORWARD_VALIDATION")
        elif float(walk.metrics.get("pass_rate", 0) or 0) < self.criteria.minimum_walk_forward_pass_rate: failures.append("WALK_FORWARD_PASS_RATE_BELOW_GATE")
        elif float(walk.metrics.get("maximum_drawdown", 0) or 0) > self.criteria.maximum_drawdown: failures.append("WALK_FORWARD_DRAWDOWN_ABOVE_GATE")
        if not stress: failures.append("MISSING_COST_STRESS_VALIDATION")
        else:
            values = [float(item.get("net_pnl", 0) or 0) for item in stress.metrics.get("scenarios", {}).values()]
            if not values or min(values) < self.criteria.minimum_cost_stress_net_pnl: failures.append("COST_STRESS_BELOW_GATE")
        if not monte: failures.append("MISSING_MONTE_CARLO_VALIDATION")
        elif float(monte.metrics.get("terminal_pnl_p05", 0) or 0) < self.criteria.minimum_monte_carlo_percentile_pnl: failures.append("MONTE_CARLO_P05_BELOW_GATE")
        if not regimes: failures.append("MISSING_REGIME_VALIDATION")
        elif int(regimes.metrics.get("coverage", 0) or 0) < self.criteria.minimum_regime_coverage: failures.append("REGIME_COVERAGE_BELOW_GATE")
        return PromotionDecision(not failures, "PAPER" if not failures else "REJECTED", {"criteria": asdict(self.criteria), "failures": failures,
            "results": {result.name: {"passed": result.passed, "metrics": result.metrics, "failures": result.failures} for result in results}})
    def advance_to_paper(self, version: str, results: list[ValidationResult]) -> PromotionDecision:
        decision = self.evaluate(results)
        if decision.approved:
            self.registry.transition(version, ModelStatus.VALIDATING); self.registry.transition(version, ModelStatus.PAPER)
        else: self.registry.transition(version, ModelStatus.REJECTED)
        return decision
    def advance_to_shadow(self, version: str) -> None:
        self.registry.transition(version, ModelStatus.SHADOW)
    def promote_live(self, version: str, shadow_observations: int, results: list[ValidationResult], actor: str) -> PromotionDecision:
        offline = self.evaluate(results); failures = list(offline.rejection_report["failures"])
        if not self.criteria.allow_live_promotion: failures.append("LIVE_PROMOTION_NOT_EXPLICITLY_ENABLED")
        if shadow_observations < self.criteria.minimum_shadow_observations: failures.append("INSUFFICIENT_SHADOW_OBSERVATIONS")
        if failures: return PromotionDecision(False, "REJECTED", {**offline.rejection_report, "failures": failures})
        self.registry.record_promotion_gate(version, offline.rejection_report)
        self.registry.transition(version, ModelStatus.APPROVED); self.registry.promote(version, actor)
        return PromotionDecision(True, "PRODUCTION", offline.rejection_report)


class ShadowEvaluationEngine:
    """Processes current market features through inference and logs hypothetical proposals only—never orders."""
    def __init__(self, inference: InferenceEngine) -> None: self.inference, self.observations = inference, []
    def observe(self, feature_snapshot: dict, regime: str, portfolio_context: dict) -> dict:
        proposal = self.inference.infer(feature_snapshot, regime, portfolio_context)
        record = {"snapshot_id": feature_snapshot["snapshot_id"], "proposal": proposal, "regime": regime, "portfolio_context": portfolio_context}
        self.observations.append(record)
        return record
    @property
    def observation_count(self) -> int: return len(self.observations)
