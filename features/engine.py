from datetime import datetime, timezone
from math import log, sqrt
from statistics import mean, pstdev

from data.types import MarketBar


class FeatureEngine:
    """Pure, deterministic features calculated only from bars available at `as_of`."""
    version = "phase3-v1"

    def calculate(self, bars: list[MarketBar], as_of: datetime, higher_timeframe_bars: list[MarketBar] | None = None) -> dict[str, float | int | str | None]:
        cutoff = as_of.astimezone(timezone.utc)
        history = sorted((bar for bar in bars if bar.timestamp <= cutoff), key=lambda bar: bar.timestamp)
        if not history:
            raise ValueError("No observations are available at the requested decision time.")
        closes, highs, lows = [b.close for b in history], [b.high for b in history], [b.low for b in history]
        current, prior = history[-1], history[-2] if len(history) > 1 else None
        result: dict[str, float | int | str | None] = {
            "feature_version": self.version, "observations": len(history), "close": current.close,
            "return_1": self._return(current.close, prior.close) if prior else None,
            "log_return_1": log(current.close / prior.close) if prior and prior.close > 0 else None,
            "range": current.high - current.low, "candle_body": abs(current.close - current.open),
            "upper_wick_ratio": self._wick(current.high - max(current.open, current.close), current),
            "lower_wick_ratio": self._wick(min(current.open, current.close) - current.low, current),
            "gap": current.open - prior.close if prior else None,
            "tick_volume": current.tick_volume, "spread": current.spread,
        }
        returns = [self._return(closes[i], closes[i - 1]) for i in range(1, len(closes))]
        result.update({"volatility_20": self._std(returns[-20:]), "momentum_10": self._return(closes[-1], closes[-11]) if len(closes) > 10 else None,
                       "roc_5": self._return(closes[-1], closes[-6]) if len(closes) > 5 else None})
        for period in (5, 10, 20, 50):
            sma, ema = self._sma(closes, period), self._ema(closes, period)
            result[f"sma_{period}"] = sma
            result[f"ema_{period}"] = ema
            result[f"distance_sma_{period}"] = self._return(current.close, sma) if sma else None
        result.update(self._atr_adx_rsi_macd_bollinger(history))
        result.update(self._structure(history))
        result.update(self._context(current, history, higher_timeframe_bars, cutoff))
        return result

    @staticmethod
    def _return(value: float, base: float) -> float | None:
        return (value - base) / base if base else None

    @staticmethod
    def _sma(values: list[float], period: int) -> float | None:
        return mean(values[-period:]) if len(values) >= period else None

    @staticmethod
    def _ema(values: list[float], period: int) -> float | None:
        if len(values) < period: return None
        multiplier, value = 2 / (period + 1), mean(values[:period])
        for item in values[period:]: value = item * multiplier + value * (1 - multiplier)
        return value

    @staticmethod
    def _std(values: list[float]) -> float | None:
        return pstdev(values) if len(values) >= 2 else None

    @staticmethod
    def _wick(wick: float, bar: MarketBar) -> float | None:
        return wick / (bar.high - bar.low) if bar.high != bar.low else 0.0

    def _atr_adx_rsi_macd_bollinger(self, bars: list[MarketBar]) -> dict:
        closes = [bar.close for bar in bars]
        true_ranges = [max(bar.high - bar.low, abs(bar.high - previous.close), abs(bar.low - previous.close))
                       for previous, bar in zip(bars, bars[1:])]
        atr = mean(true_ranges[-14:]) if len(true_ranges) >= 14 else None
        gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
        rsi = None
        if len(gains) >= 14:
            average_loss = mean(losses[-14:]); rsi = 100 if average_loss == 0 else 100 - 100 / (1 + mean(gains[-14:]) / average_loss)
        ema12, ema26 = self._ema(closes, 12), self._ema(closes, 26)
        middle = self._sma(closes, 20); std = self._std(closes[-20:]) if len(closes) >= 20 else None
        directional = [abs(bars[i].close - bars[i - 1].close) / true_ranges[i - 1] for i in range(1, len(bars)) if true_ranges[i - 1] > 0]
        return {"atr_14": atr, "rsi_14": rsi, "macd": ema12 - ema26 if ema12 is not None and ema26 is not None else None,
                "adx_proxy_14": mean(directional[-14:]) * 100 if len(directional) >= 14 else None,
                "bollinger_middle_20": middle, "bollinger_width_20": std * 4 if std is not None else None,
                "bollinger_zscore_20": (closes[-1] - middle) / std if middle is not None and std not in (None, 0) else None}

    def _structure(self, bars: list[MarketBar]) -> dict:
        window = bars[-21:]
        prior = window[:-1]
        swing_high = max((bar.high for bar in prior), default=None); swing_low = min((bar.low for bar in prior), default=None)
        current = bars[-1]
        prior_ranges = [bar.high - bar.low for bar in prior[-10:]]
        average_range = mean(prior_ranges) if prior_ranges else None
        return {"rolling_high_20": swing_high, "rolling_low_20": swing_low,
                "break_structure_up": int(swing_high is not None and current.close > swing_high),
                "break_structure_down": int(swing_low is not None and current.close < swing_low),
                "trend_structure": "UP" if len(bars) > 5 and current.close > bars[-6].close else "DOWN" if len(bars) > 5 and current.close < bars[-6].close else "RANGE",
                "support_proximity": self._return(current.close, swing_low) if swing_low else None,
                "resistance_proximity": self._return(current.close, swing_high) if swing_high else None,
                "breakout_strength": (current.high - current.low) / average_range if average_range else None,
                "range_compression": (current.high - current.low) / average_range if average_range else None,
                "range_expansion": int(bool(average_range and current.high - current.low > average_range * 1.5))}

    def _context(self, current: MarketBar, bars: list[MarketBar], higher: list[MarketBar] | None, cutoff: datetime) -> dict:
        timestamp = current.timestamp.astimezone(timezone.utc)
        spreads = sorted(bar.spread for bar in bars[-100:] if bar.spread is not None)
        volatility = [bar.high - bar.low for bar in bars[-100:]]
        higher_history = sorted((bar for bar in (higher or []) if bar.timestamp <= cutoff), key=lambda bar: bar.timestamp)
        return {"session": "ASIA" if timestamp.hour < 7 else "LONDON" if timestamp.hour < 13 else "NEW_YORK" if timestamp.hour < 21 else "OFF_HOURS",
                "day_of_week": timestamp.weekday(), "hour_utc": timestamp.hour,
                "higher_timeframe_trend": "UP" if len(higher_history) > 1 and higher_history[-1].close > higher_history[-2].close else "DOWN" if len(higher_history) > 1 else "UNKNOWN",
                "spread_percentile": self._percentile(current.spread, spreads),
                "volatility_percentile": self._percentile(current.high - current.low, sorted(volatility))}

    @staticmethod
    def _percentile(value: float | None, ordered: list[float]) -> float | None:
        if value is None or not ordered: return None
        return sum(item <= value for item in ordered) / len(ordered)
