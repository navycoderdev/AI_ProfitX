from datetime import datetime, timedelta

from ai.oos_simulation_v2 import simulate_locked_oos_v2
from ai.oos_simulation_v3 import simulate_locked_oos_v3
from ai.types import ProposalAction
from backtesting.symbol_economics import SymbolEconomics


class FixedModel:
    version = "Brain-v2"

    def probabilities(self, features):
        return {ProposalAction.LONG: .8, ProposalAction.SHORT: .1, ProposalAction.NO_TRADE: .1}


def test_gold_contract_and_closed_session_unfilled_signal():
    at = datetime(2026, 9, 10, 22)
    base = {"symbol": "XAUUSD", "split": "OOS", "features": {}, "exit_close": 4002.,
        "target_raw_data_cutoff": at + timedelta(hours=1)}
    rows = [{**base, "timestamp": at, "entry_open": 4000., "entry_spread_points": 60},
        {**base, "timestamp": at + timedelta(hours=2), "target_raw_data_cutoff": at + timedelta(hours=3),
            "entry_open": None, "entry_spread_points": None}]
    contracts = {symbol: SymbolEconomics(symbol, 100., .01, .01, "USD", .01)
        for symbol in ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD")}
    result = simulate_locked_oos_v2(FixedModel(), rows, threshold=.4, contracts=contracts)
    assert result["simulated_trade_count"] == 1
    assert result["unfilled_no_entry_candle"] == 1
    assert result["gross_pnl_usd"] == 2.
    assert result["transaction_costs_usd"] == .6
    assert result["slippage_usd"] == .02


def test_brain_v3_six_symbol_economics_are_required_and_reported():
    model = FixedModel()
    model.version = "Brain-v3"
    at = datetime(2026, 9, 20, 12)
    rows = [{"symbol": "BTCUSD", "split": "OOS", "features": {}, "timestamp": at,
             "entry_open": 80000., "entry_spread_points": 500, "exit_close": 80100.,
             "target_raw_data_cutoff": at + timedelta(hours=1)}]
    contracts = {symbol: SymbolEconomics(symbol, 1., .01, .01, "USD", .01)
        for symbol in ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "ETHUSD")}
    result = simulate_locked_oos_v3(model, rows, threshold=.4, contracts=contracts)
    assert result["prediction_count"] == 1
    assert result["per_symbol"]["BTCUSD"]["simulated_trade_count"] == 1
    assert result["per_symbol"]["EURUSD"]["coverage_status"] == "NO_OOS_ROWS"
