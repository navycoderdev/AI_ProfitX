from dataclasses import dataclass
from statistics import mean

from backtesting.types import Side
from data.types import MarketBar


@dataclass(frozen=True, slots=True)
class BaselineSignal:
    side: Side | None
    stop_loss: float | None = None
    take_profit: float | None = None
    reason: str = "no_signal"


class MovingAverageTrendBaseline:
    version = "ma-trend-baseline-v1"
    def __init__(self, fast: int = 10, slow: int = 30) -> None: self.fast, self.slow = fast, slow
    def evaluate(self, history: list[MarketBar]) -> BaselineSignal:
        if len(history) < self.slow + 1: return BaselineSignal(None)
        closes = [bar.close for bar in history]; fast, slow = mean(closes[-self.fast:]), mean(closes[-self.slow:])
        previous_fast, previous_slow = mean(closes[-self.fast - 1:-1]), mean(closes[-self.slow - 1:-1])
        if previous_fast <= previous_slow < fast: return self._signal(Side.LONG, history, "bullish_ma_cross")
        if previous_fast >= previous_slow > fast: return self._signal(Side.SHORT, history, "bearish_ma_cross")
        return BaselineSignal(None)
    @staticmethod
    def _signal(side: Side, history: list[MarketBar], reason: str) -> BaselineSignal:
        atr = mean(bar.high - bar.low for bar in history[-14:]); price = history[-1].close
        return BaselineSignal(side, price - atr * 2 if side is Side.LONG else price + atr * 2,
                              price + atr * 3 if side is Side.LONG else price - atr * 3, reason)


class BreakoutBaseline:
    version = "breakout-baseline-v1"
    def __init__(self, lookback: int = 20) -> None: self.lookback = lookback
    def evaluate(self, history: list[MarketBar]) -> BaselineSignal:
        if len(history) <= self.lookback: return BaselineSignal(None)
        current, prior = history[-1], history[-self.lookback - 1:-1]
        high, low = max(bar.high for bar in prior), min(bar.low for bar in prior)
        if current.close > high: return MovingAverageTrendBaseline._signal(Side.LONG, history, "range_breakout_up")
        if current.close < low: return MovingAverageTrendBaseline._signal(Side.SHORT, history, "range_breakout_down")
        return BaselineSignal(None)


class MeanReversionBaseline:
    version = "mean-reversion-baseline-v1"
    def __init__(self, period: int = 20, threshold: float = 2.0) -> None: self.period, self.threshold = period, threshold
    def evaluate(self, history: list[MarketBar]) -> BaselineSignal:
        if len(history) < self.period: return BaselineSignal(None)
        closes = [bar.close for bar in history[-self.period:]]; middle = mean(closes)
        deviation = (sum((close - middle) ** 2 for close in closes) / len(closes)) ** .5
        if deviation == 0: return BaselineSignal(None)
        zscore = (closes[-1] - middle) / deviation
        if zscore <= -self.threshold: return MovingAverageTrendBaseline._signal(Side.LONG, history, "mean_reversion_low")
        if zscore >= self.threshold: return MovingAverageTrendBaseline._signal(Side.SHORT, history, "mean_reversion_high")
        return BaselineSignal(None)
