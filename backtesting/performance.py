from math import sqrt
from statistics import mean, pstdev

from backtesting.types import SimulatedTrade


class PerformanceAnalyzer:
    def analyze(self, starting_cash: float, trades: list[SimulatedTrade], equity_curve: list[dict]) -> dict:
        pnl = [trade.net_pnl for trade in trades]; wins = [value for value in pnl if value > 0]; losses = [value for value in pnl if value < 0]
        gross_profit, gross_loss = sum(wins), abs(sum(losses)); returns = [row["return"] for row in equity_curve[1:]]
        peak, maximum_drawdown = starting_cash, 0.0
        for row in equity_curve:
            peak = max(peak, row["equity"]); maximum_drawdown = max(maximum_drawdown, (peak - row["equity"]) / peak if peak else 0)
        average_return, deviation = (mean(returns), pstdev(returns)) if len(returns) >= 2 else (0.0, 0.0)
        downside = [min(value, 0) for value in returns]; downside_dev = sqrt(mean(value * value for value in downside)) if downside else 0.0
        duration = [trade.holding_seconds for trade in trades]
        return {"net_pnl": sum(pnl), "gross_profit": gross_profit, "gross_loss": gross_loss,
                "win_rate": len(wins) / len(trades) if trades else 0.0, "average_win": mean(wins) if wins else 0.0,
                "average_loss": mean(losses) if losses else 0.0, "profit_factor": gross_profit / gross_loss if gross_loss else None,
                "expectancy": mean(pnl) if pnl else 0.0, "maximum_drawdown": maximum_drawdown,
                "sharpe_like": average_return / deviation * sqrt(len(returns)) if deviation else None,
                "sortino_like": average_return / downside_dev * sqrt(len(returns)) if downside_dev else None,
                "trade_count": len(trades), "average_holding_seconds": mean(duration) if duration else 0.0,
                "mfe": sum(trade.mfe for trade in trades), "mae": sum(trade.mae for trade in trades),
                "transaction_costs": sum(trade.transaction_costs for trade in trades),
                "slippage_impact": sum(trade.slippage_impact for trade in trades),
                "exposure": sum(duration) / ((equity_curve[-1]["timestamp"] - equity_curve[0]["timestamp"]).total_seconds()) if len(equity_curve) > 1 else 0.0}
