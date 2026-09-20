"""Explicit USD-account offline P&L from verified MT5 contract metadata."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolEconomics:
    symbol: str
    contract_size: float
    point: float
    tick_size: float
    profit_currency: str
    lots: float
    slippage_ticks_each_side: float = 1.0

    @classmethod
    def from_mt5_probe(cls, symbol: str, metadata: dict) -> "SymbolEconomics":
        if metadata["canonical_symbol"] != symbol or metadata["broker_symbol"] != symbol:
            raise ValueError("Contract metadata does not match the canonical broker symbol.")
        values = cls(symbol, float(metadata["contract_size"]), float(metadata["point"]),
                     float(metadata["tick_size"]), metadata["currency_profit"], float(metadata["volume_min"]))
        if min(values.contract_size, values.point, values.tick_size, values.lots) <= 0:
            raise ValueError("MT5 contract metadata must contain positive economics.")
        if values.profit_currency not in {"USD", "JPY"}:
            raise ValueError("Unsupported profit currency for USD-account simulation.")
        if values.profit_currency == "JPY" and symbol != "USDJPY":
            raise ValueError("JPY profit conversion is verified only for USDJPY.")
        observed = float(metadata["mt5_profit_per_tick_buy_one_lot"])
        implied = values.tick_size * values.contract_size
        if values.profit_currency == "USD" and abs(observed - implied) > max(.01, implied * .02):
            raise ValueError("MT5 profit calculator disagrees with contract-size P&L.")
        return values

    def evaluate(self, side: str, entry: float, exit_price: float, spread_points: float) -> dict[str, float]:
        if side not in {"LONG", "SHORT"} or min(entry, exit_price) <= 0 or spread_points < 0:
            raise ValueError("Trade side, observed prices, and spread must be valid.")
        conversion = 1 / exit_price if self.profit_currency == "JPY" else 1.0
        scale = self.contract_size * self.lots * conversion
        direction = 1 if side == "LONG" else -1
        gross = direction * (exit_price - entry) * scale
        spread = spread_points * self.point * scale
        slippage = 2 * self.slippage_ticks_each_side * self.tick_size * scale
        return {"gross_pnl_usd": gross, "spread_cost_usd": spread, "slippage_usd": slippage,
                "net_pnl_usd": gross - spread - slippage}
