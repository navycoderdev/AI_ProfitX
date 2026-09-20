from datetime import datetime, timedelta, timezone

from ai.oos_simulation import simulate_locked_oos
from ai.types import ProposalAction
from database.base import Base
from database.models import BacktestRun
from monitoring.control_center import ControlCenter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class FixedModel:
    version = "Brain-v1"

    def probabilities(self, features):
        return {ProposalAction.LONG: .8, ProposalAction.SHORT: .1, ProposalAction.NO_TRADE: .1}


def test_oos_simulation_uses_real_open_and_exit_and_keeps_missing_symbols_explicit():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = [{"symbol": "EURUSD", "split": "OOS", "timestamp": start,
             "target_raw_data_cutoff": start + timedelta(hours=1), "features": {},
             "entry_open": 1.1, "exit_close": 1.101},
            {"symbol": "EURUSD", "split": "OOS", "timestamp": start + timedelta(minutes=5),
             "target_raw_data_cutoff": start + timedelta(hours=1, minutes=5), "features": {},
             "entry_open": 1.1, "exit_close": 1.102}]
    result = simulate_locked_oos(FixedModel(), rows, threshold=.40, point_size=.00001,
                                 spread_points=1, slippage_points_each_side=2)
    assert result["prediction_count"] == 2
    assert result["simulated_trade_count"] == 1
    assert result["skipped_overlapping_signals"] == 1
    assert abs(result["gross_pnl_usd"] - 1.0) < 1e-9
    assert abs(result["transaction_costs_usd"] - .01) < 1e-9
    assert abs(result["slippage_usd"] - .04) < 1e-9
    assert result["per_symbol"]["USDJPY"]["coverage_status"] == "NO_OOS_ROWS"


def test_control_center_reads_only_persisted_offline_simulation():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions() as session:
        session.add(BacktestRun(strategy_version="Brain-v1", symbol="ALL", timeframe="M5",
            data_version="frozen-hash", configuration={}, results={"trade_scope": "OFFLINE_OOS_SIMULATION_TRADES",
                "net_pnl_usd": -3.5, "runtime_paper_trades": 0}))
        session.commit()
    with sessions() as session:
        result = ControlCenter._oos_simulations(session)["Brain-v1"]
    assert result["net_pnl_usd"] == -3.5
    assert result["runtime_paper_trades"] == 0
    assert result["run_id"]
