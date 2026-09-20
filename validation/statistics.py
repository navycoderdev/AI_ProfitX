from random import Random

from backtesting.types import BacktestReport
from validation.types import ValidationResult


class MonteCarloAnalyzer:
    """Seeded bootstrap for uncertainty analysis, not a forecast of future returns."""
    def analyze(self, report: BacktestReport, simulations: int = 250, seed: int = 17) -> ValidationResult:
        pnl = [trade.net_pnl for trade in report.trades]
        if not pnl: return ValidationResult("monte_carlo", False, {"simulations": simulations}, ("NO_TRADES_FOR_BOOTSTRAP",))
        rng = Random(seed); terminal, drawdowns = [], []
        for _ in range(simulations):
            path = [rng.choice(pnl) for _ in pnl]; cumulative = peak = drawdown = 0.0
            for value in path:
                cumulative += value; peak = max(peak, cumulative); drawdown = max(drawdown, peak - cumulative)
            terminal.append(cumulative); drawdowns.append(drawdown)
        terminal.sort(); drawdowns.sort(); index = max(0, int(.05 * len(terminal)) - 1)
        return ValidationResult("monte_carlo", terminal[index] > 0, {"simulations": simulations, "seed": seed,
            "terminal_pnl_p05": terminal[index], "terminal_pnl_median": terminal[len(terminal) // 2],
            "max_drawdown_p95": drawdowns[min(len(drawdowns) - 1, int(.95 * len(drawdowns)))]},
            () if terminal[index] > 0 else ("NEGATIVE_BOOTSTRAP_P05",))
