from datetime import datetime, timedelta, timezone

import pytest

from backtesting.engine import BacktestEngine
from backtesting.types import BacktestConfig, CostAssumptions
from data.quality import DataQualityEngine
from data.storage import RawMarketDataRepository
from data.types import MarketBar
from database.session import initialize_database
from config.settings import Settings
from strategies.baselines import MovingAverageTrendBaseline


UTC = timezone.utc


def bars(count=40):
    start = datetime(2026, 1, 5, tzinfo=UTC)
    return [MarketBar("TEST", "EURUSD", "M5", start + timedelta(minutes=5*i), 1+i*.0001, 1.001+i*.0001, .999+i*.0001, 1+i*.0001, 1, 1, 1) for i in range(count)]


def test_resume_after_interruption_is_idempotent(tmp_path):
    repo = RawMarketDataRepository(initialize_database(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path/'resume.db'}")))
    all_bars = bars(12)
    assert repo.append_bars(all_bars[:5]) == 5  # interrupted after first chunk
    assert repo.append_bars(all_bars[5:]) == 7  # resumed
    assert repo.append_bars(all_bars) == 0
    assert len(repo.bars_as_of("EURUSD", "M5", all_bars[-1].timestamp)) == 12


def test_duplicate_and_out_of_order_are_data_errors():
    items = bars(3)
    duplicate = DataQualityEngine().report("EURUSD", "M5", [items[0], items[0], items[1]])
    unordered = DataQualityEngine().report("EURUSD", "M5", [items[1], items[0], items[2]])
    assert duplicate.duplicates > 0 and any(error["reason"] == "duplicate_timestamp" for error in duplicate.data_errors)
    assert any(error["reason"] == "out_of_order_timestamp" for error in unordered.data_errors)


def test_fixed_backtest_inputs_are_deterministic():
    config = BacktestConfig("EURUSD", "M5", costs=CostAssumptions(slippage_points=1))
    first = BacktestEngine().run(bars(50), MovingAverageTrendBaseline(fast=2, slow=3), config)
    second = BacktestEngine().run(bars(50), MovingAverageTrendBaseline(fast=2, slow=3), config)
    assert first.metrics == second.metrics
    assert [(t.side, t.entry_price, t.exit_price, t.net_pnl) for t in first.trades] == [(t.side, t.entry_price, t.exit_price, t.net_pnl) for t in second.trades]
