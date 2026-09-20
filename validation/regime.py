from validation.types import ValidationResult


class RegimePerformanceAnalyzer:
    def analyze(self, performance_by_regime: dict) -> ValidationResult:
        resolved = {name: metrics for name, metrics in performance_by_regime.items() if name != "UNCERTAIN" and metrics.get("executed_trades", 0) > 0}
        coverage = len(resolved); failures = []
        if coverage < 2: failures.append("INSUFFICIENT_REGIME_COVERAGE")
        negative = [name for name, metrics in resolved.items() if float(metrics.get("net_pnl_after_costs", 0) or 0) <= 0]
        if negative: failures.append("ADVERSE_REGIME_PERFORMANCE")
        return ValidationResult("regime_performance", not failures, {"coverage": coverage, "negative_regimes": negative, "regimes": performance_by_regime}, tuple(failures))
