"""TRAIN/VALIDATION-only Brain-v4 research and symbol-aware risk design."""

from dataclasses import asdict, dataclass
from math import cos, pi, sin


FAMILIES = {"EURUSD": "FX", "GBPUSD": "FX", "USDJPY": "FX", "XAUUSD": "METAL",
            "BTCUSD": "CRYPTO", "ETHUSD": "CRYPTO"}


def enriched_features(row: dict, contracts: dict) -> dict[str, float]:
    """Decision-time features normalized without labels or future prices."""
    values = dict(row["features"])
    symbol = row["symbol"]; contract = contracts[symbol]
    midpoint = max((values["rolling_high_20"] + values["rolling_low_20"]) / 2, 1e-12)
    atr = max(values["atr_14"], contract["point"])
    hour = values["hour_utc"]
    trends = [values[key] for key in ("m15_trend", "m30_trend", "h1_trend", "h4_trend")]
    values.update({
        "atr_price_fraction": atr / midpoint,
        "range_price_fraction": (values["rolling_high_20"] - values["rolling_low_20"]) / midpoint,
        "body_atr_fraction": values["candle_body"] / atr,
        "broker_spread_price_fraction": contract["spread_points_snapshot"] * contract["point"] / midpoint,
        "broker_spread_atr_fraction": contract["spread_points_snapshot"] * contract["point"] / atr,
        "session_hour_sin": sin(2 * pi * hour / 24), "session_hour_cos": cos(2 * pi * hour / 24),
        "trend_alignment": sum(trends) / 4, "trend_agreement": abs(sum(trends)) / 4,
        "trend_volatility_interaction": (sum(trends) / 4) * values["volatility_percentile"],
    })
    return values


def head_key(symbol: str, architecture: str) -> str:
    if architecture.startswith("shared"): return "ALL"
    if architecture == "asset_family_heads": return FAMILIES[symbol]
    if architecture == "symbol_heads": return symbol
    raise ValueError(f"Unknown architecture: {architecture}")


@dataclass(frozen=True)
class SymbolRiskPolicy:
    family: str
    stop_atr_multiple: float
    target_atr_multiple: float
    maximum_spread_atr_fraction: float
    maximum_spread_cost_risk_fraction: float
    minimum_risk_reward: float = 1.5
    risk_per_trade_fraction: float = .01
    maximum_drawdown_fraction: float = .10
    maximum_daily_loss_fraction: float = .03
    maximum_weekly_loss_fraction: float = .06
    maximum_consecutive_losses: int = 4
    maximum_open_positions: int = 3


POLICIES = {
    "FX": SymbolRiskPolicy("FX", 1.5, 2.25, .20, .10),
    "XAUUSD": SymbolRiskPolicy("XAUUSD", 1.8, 2.70, .15, .10),
    "BTCUSD": SymbolRiskPolicy("BTCUSD", 2.0, 3.00, .12, .10),
    "ETHUSD": SymbolRiskPolicy("ETHUSD", 2.0, 3.00, .12, .10),
}


def policy_for(symbol: str) -> SymbolRiskPolicy:
    return POLICIES["FX"] if FAMILIES[symbol] == "FX" else POLICIES[symbol]


def stop_target(symbol: str, direction: str, entry: float, atr: float, contract: dict) -> dict:
    policy = policy_for(symbol)
    tick = float(contract["tick_size"])
    if direction not in {"LONG", "SHORT"} or min(entry, atr, tick) <= 0:
        raise ValueError("A directional proposal requires positive entry, ATR, and broker tick size.")
    stop_distance = max(policy.stop_atr_multiple * atr, tick)
    target_distance = max(policy.target_atr_multiple * atr, tick)
    sign = 1 if direction == "LONG" else -1
    rounded = lambda value: round(round(value / tick) * tick, int(contract["digits"]))
    return {"entry": entry, "stop_loss": rounded(entry - sign * stop_distance),
            "take_profit": rounded(entry + sign * target_distance), "atr": atr,
            "stop_distance": stop_distance, "target_distance": target_distance,
            "risk_reward": target_distance / stop_distance, "policy": asdict(policy),
            "contract": {key: contract[key] for key in ("point", "tick_size", "contract_size", "volume_min", "currency_profit")}}


def normalized_spread_gate(symbol: str, spread_points: float, atr: float, entry: float,
                           equity: float, contract: dict) -> dict:
    policy = policy_for(symbol)
    spread_price = spread_points * float(contract["point"])
    spread_atr_fraction = spread_price / atr if atr > 0 else float("inf")
    conversion = 1 / entry if contract["currency_profit"] == "JPY" else 1.0
    spread_cost = spread_price * float(contract["contract_size"]) * float(contract["volume_min"]) * conversion
    risk_budget = equity * policy.risk_per_trade_fraction
    cost_risk_fraction = spread_cost / risk_budget if risk_budget > 0 else float("inf")
    approved = (spread_atr_fraction <= policy.maximum_spread_atr_fraction and
                cost_risk_fraction <= policy.maximum_spread_cost_risk_fraction)
    return {"approved": approved, "spread_price": spread_price,
            "spread_atr_fraction": spread_atr_fraction, "spread_cost_usd": spread_cost,
            "spread_cost_risk_fraction": cost_risk_fraction,
            "limits": {"maximum_spread_atr_fraction": policy.maximum_spread_atr_fraction,
                       "maximum_spread_cost_risk_fraction": policy.maximum_spread_cost_risk_fraction}}
